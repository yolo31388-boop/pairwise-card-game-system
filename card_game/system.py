"""
pairwise-card-game-system - CardGameSystem

A Hearthstone-style card battle core. Fixes implemented:

* play_card():            mana cost check, board-size limit, play conditions
* trigger_card_effect():  per-timing effects (battlecry != deathrattle),
                          explicit target selection, chained resolution
* draw_card():            deck shuffling, 10-card hand limit (overdraw burns),
                          escalating fatigue damage on an empty deck
* gain_mana():            10-crystal cap, per-turn reset, permanent crystals
                          kept separate from temporary coin-style bonus mana
* end_turn():             end-of-turn triggers, active-player switch,
                          turn timeout enforcement
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

MAX_MANA_CRYSTALS = 10
MAX_HAND_SIZE = 10
MAX_BOARD_SIZE = 7
TURN_TIME_LIMIT = 75.0  # seconds per turn

# Effect timings: each effect is bound to exactly one timing.
BATTLECRY = "battlecry"
DEATHRATTLE = "deathrattle"
END_OF_TURN = "end_of_turn"
TIMINGS = (BATTLECRY, DEATHRATTLE, END_OF_TURN)


@dataclass
class Card:
    name: str
    cost: int
    attack: int = 0
    health: int = 0
    # timing -> list of effect dicts; battlecry and deathrattle are distinct.
    effects: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Play condition: requires a friendly minion already on the board.
    requires_friendly_minion: bool = False

    def copy(self) -> "Card":
        return Card(
            self.name,
            self.cost,
            self.attack,
            self.health,
            {timing: [dict(effect) for effect in effects]
             for timing, effects in self.effects.items()},
            self.requires_friendly_minion,
        )


@dataclass
class Player:
    name: str
    deck: list[Card] = field(default_factory=list)
    hand: list[Card] = field(default_factory=list)
    board: list[Card] = field(default_factory=list)
    hp: int = 30
    mana_crystals: int = 0
    mana: int = 0
    # Temporary coin-style mana, tracked separately from real crystals.
    bonus_mana: int = 0
    fatigue: int = 0
    burned: list[Card] = field(default_factory=list)

    @property
    def available_mana(self) -> int:
        return self.mana + self.bonus_mana


def _damage(system: "CardGameSystem", owner: Player, target: Any, amount: int) -> None:
    """Deal damage to a hero or minion; a dead minion queues its deathrattle."""
    if target is None:
        return
    if isinstance(target, Player):
        target.hp -= amount
    elif isinstance(target, Card):
        target.health -= amount
        if target.health <= 0:
            for player in system.players:
                if target in player.board:
                    player.board.remove(target)
                    # Chain: dying queues the minion's deathrattle effects.
                    system.queue_effect(target, DEATHRATTLE, owner=player)
                    break


def damage_effect(amount: int) -> dict[str, Any]:
    def action(system: "CardGameSystem", owner: Player, source: Card, target: Any) -> None:
        if target is None:
            target = system.opponent_of(owner)
        _damage(system, owner, target, amount)

    return {"action": action, "needs_target": True}


def heal_effect(amount: int) -> dict[str, Any]:
    def action(system: "CardGameSystem", owner: Player, source: Card, target: Any) -> None:
        if target is None:
            target = owner
        if isinstance(target, Player):
            target.hp += amount
        elif isinstance(target, Card):
            target.health += amount

    return {"action": action, "needs_target": True, "targeting": "friendly"}


def draw_effect(count: int = 1) -> dict[str, Any]:
    def action(system: "CardGameSystem", owner: Player, source: Card, target: Any) -> None:
        system.draw_card(owner, count)

    return {"action": action}


class CardGameSystem:
    def __init__(self, players: list[Player] | None = None, seed: int | None = None):
        self.state: dict[str, Any] = {}
        self._rng = random.Random(seed)
        self._effect_queue: list[tuple[Card, str, Any, Player]] = []
        self.resolved_effects: list[tuple[str, str]] = []
        self.log: list[str] = []

        if players is None:
            players = [
                self._default_player("Player1", seed),
                self._default_player("Player2", None if seed is None else seed + 1),
            ]
        self.players = players
        for player in self.players:
            self.shuffle_deck(player)

        self.turn_count = 0
        self.current_index = 0
        self.turn_start_time = time.monotonic()

        first = self.current_player
        first.mana_crystals = 1
        first.mana = 1
        self.draw_card(first, 1)

    # ------------------------------------------------------------------ setup

    @staticmethod
    def _default_player(name: str, seed: int | None) -> Player:
        templates = [
            Card("River Crocolisk", 2, 2, 3),
            Card("Chillwind Yeti", 4, 4, 5),
            Card("Flame Imp", 1, 3, 2,
                 effects={BATTLECRY: [damage_effect(3)]}),
            Card("Engineer", 2, 1, 1,
                 effects={BATTLECRY: [draw_effect(1)]}),
            Card("Reckless Bomber", 2, 1, 2,
                 effects={BATTLECRY: [damage_effect(1)],
                          DEATHRATTLE: [damage_effect(2)]}),
            Card("Field Medic", 2, 1, 3,
                 effects={BATTLECRY: [heal_effect(2)]}),
            Card("Squad Leader", 3, 2, 3,
                 effects={END_OF_TURN: [draw_effect(1)]},
                 requires_friendly_minion=True),
        ]
        deck = [template.copy() for template in templates for _ in range(3)]
        random.Random(seed).shuffle(deck)
        return Player(name=name, deck=deck)

    @property
    def current_player(self) -> Player:
        return self.players[self.current_index]

    def opponent_of(self, player: Player) -> Player:
        return self.players[1 - self.players.index(player)]

    # --------------------------------------------------------------- playing

    def play_card(
        self,
        player: Player | None = None,
        card: Card | None = None,
        target: Any = None,
    ) -> dict[str, Any]:
        """Play a card, enforcing mana cost, board limit and play conditions."""
        player = player or self.current_player

        if card is None:
            card = next(
                (candidate for candidate in player.hand
                 if candidate.cost <= player.available_mana
                 and len(player.board) < MAX_BOARD_SIZE
                 and (not candidate.requires_friendly_minion or player.board)),
                None,
            )

        result: dict[str, Any] = {"success": False, "card": card, "reason": None}
        if card is None or card not in player.hand:
            result["reason"] = "no_playable_card"
            return result

        # 1) Mana check: cannot play what you cannot afford.
        if card.cost > player.available_mana:
            result["reason"] = "insufficient_mana"
            return result

        # 2) Board limit: no playing onto a full board.
        if len(player.board) >= MAX_BOARD_SIZE:
            result["reason"] = "board_full"
            return result

        # 3) Play condition: e.g. needs a friendly minion already in play.
        if card.requires_friendly_minion and not player.board:
            result["reason"] = "condition_not_met"
            return result

        # Pay the cost; temporary bonus mana is spent before real crystals.
        from_bonus = min(player.bonus_mana, card.cost)
        player.bonus_mana -= from_bonus
        player.mana -= card.cost - from_bonus

        player.hand.remove(card)
        player.board.append(card)
        self.log.append(f"{player.name} plays {card.name}")

        # Timing: entering play fires only the card's battlecry effects.
        self.trigger_card_effect(card, BATTLECRY, target=target, owner=player)

        result["success"] = True
        result["reason"] = None
        return result

    # --------------------------------------------------------------- effects

    def queue_effect(
        self,
        card: Card,
        timing: str,
        target: Any = None,
        owner: Player | None = None,
    ) -> None:
        """Enqueue an effect so chained triggers resolve in the same pass."""
        if timing not in TIMINGS:
            raise ValueError(f"unknown effect timing: {timing!r}")
        self._effect_queue.append((card, timing, target, owner or self.current_player))

    def trigger_card_effect(
        self,
        card: Card | None = None,
        timing: str = BATTLECRY,
        target: Any = None,
        owner: Player | None = None,
    ) -> list[tuple[str, str]]:
        """Resolve effects for one timing, with target choice and chaining."""
        if card is not None:
            self.queue_effect(card, timing, target, owner)

        # Drain the queue: effects triggered by other effects (chains) are
        # resolved here instead of being dropped.
        while self._effect_queue:
            source, effect_timing, requested_target, effect_owner = self._effect_queue.pop(0)
            for effect in source.effects.get(effect_timing, []):
                chosen = requested_target
                if effect.get("needs_target") and chosen is None:
                    chosen = self.select_target(effect_owner, effect)
                effect["action"](self, effect_owner, source, chosen)
                self.resolved_effects.append((source.name, effect_timing))
        return self.resolved_effects

    def select_target(self, owner: Player, effect: dict[str, Any]) -> Any:
        """Deterministic target selection (never a random target)."""
        if effect.get("targeting") == "friendly":
            return owner.board[0] if owner.board else owner
        enemy = self.opponent_of(owner)
        if enemy.board:
            return max(enemy.board, key=lambda minion: minion.attack)
        return enemy

    # ---------------------------------------------------------------- drawing

    def shuffle_deck(self, player: Player | None = None) -> None:
        """Shuffle the deck so draw order is not fixed."""
        player = player or self.current_player
        self._rng.shuffle(player.deck)

    def draw_card(self, player: Player | None = None, count: int = 1) -> list[Card]:
        """Draw cards; burn over the 10-card hand limit, fatigue on empty deck."""
        player = player or self.current_player
        drawn: list[Card] = []
        for _ in range(count):
            if not player.deck:
                # Fatigue: escalating damage when drawing from an empty deck.
                player.fatigue += 1
                player.hp -= player.fatigue
                self.log.append(f"{player.name} takes {player.fatigue} fatigue damage")
                continue
            card = player.deck.pop(0)
            if len(player.hand) >= MAX_HAND_SIZE:
                # Hand limit: the drawn card is burned, not added.
                player.burned.append(card)
                self.log.append(f"{player.name} burns {card.name} (hand full)")
                continue
            player.hand.append(card)
            drawn.append(card)
        return drawn

    # ------------------------------------------------------------------ mana

    def gain_mana(
        self,
        player: Player | None = None,
        amount: int = 1,
        temporary: bool = False,
    ) -> int:
        """Gain mana; permanent crystals cap at 10, coin mana is temporary."""
        player = player or self.current_player
        if amount <= 0:
            return 0

        if temporary:
            # Coin-style bonus mana: this turn only, never grows crystals.
            player.bonus_mana += amount
            self.log.append(f"{player.name} gains {amount} temporary mana")
            return amount

        gained = min(amount, MAX_MANA_CRYSTALS - player.mana_crystals)
        player.mana_crystals += gained
        player.mana = min(player.mana + gained, player.mana_crystals)
        return gained

    # ----------------------------------------------------------------- turns

    def turn_timed_out(self, now: float | None = None) -> bool:
        """True when the current turn has exceeded the time limit."""
        if now is None:
            now = time.monotonic()
        return now - self.turn_start_time >= TURN_TIME_LIMIT

    def check_timeout(self) -> dict[str, Any] | None:
        """Force-end the turn if the player ran out of time."""
        if self.turn_timed_out():
            return self.end_turn()
        return None

    def end_turn(self) -> dict[str, Any]:
        """Resolve end-of-turn triggers, reset mana, switch active player."""
        outgoing = self.current_player

        # 1) End-of-turn triggers (and any chains they cause) resolve first.
        for card in list(outgoing.board):
            self.trigger_card_effect(card, END_OF_TURN, owner=outgoing)

        # 2) Mana reset: unspent crystals and coin mana do not carry over.
        outgoing.mana = 0
        outgoing.bonus_mana = 0
        self.log.append(f"{outgoing.name} ends turn {self.turn_count + 1}")

        # 3) Switch to the other player and start their turn.
        self.current_index = (self.current_index + 1) % len(self.players)
        self.turn_count += 1
        self.turn_start_time = time.monotonic()

        incoming = self.current_player
        self.gain_mana(incoming, 1)
        incoming.mana = incoming.mana_crystals
        self.draw_card(incoming, 1)

        return {
            "turn": self.turn_count,
            "previous_player": outgoing.name,
            "current_player": incoming.name,
            "timed_out": False,
        }
