"""
pairwise-card-game-system - CardGameSystem

Card battle core implementing:

* play_card():            mana check, board cap, play conditions
* trigger_card_effect():  effect timing (battlecry / deathrattle / end of
                          turn), explicit target selection, chained resolution
* draw_card():            deck shuffling, 10-card hand cap (overdraw burns),
                          escalating fatigue damage when the deck is empty
* gain_mana():            10-crystal cap, turn reset, permanent crystals vs.
                          temporary coin-style bonus mana
* end_turn():             end-of-turn effects, mana reset/expire, player
                          switch, turn timeout enforcement
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable


MAX_MANA_CRYSTALS = 10
MAX_HAND_SIZE = 10
MAX_BOARD_SIZE = 7
TURN_TIME_LIMIT = 75.0

# Effect timings
BATTLECRY = "battlecry"
DEATHRATTLE = "deathrattle"
END_OF_TURN = "end_of_turn"


@dataclass
class Card:
    name: str
    cost: int
    attack: int = 0
    health: int = 0
    # timing -> list of effects, e.g. {"battlecry": [effect, ...]}
    effects: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Play condition: a friendly minion must already be on the board.
    requires_board: bool = False

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Card {self.name} ({self.cost})>"


@dataclass
class Player:
    name: str
    deck: list[Card] = field(default_factory=list)
    hand: list[Card] = field(default_factory=list)
    board: list[Card] = field(default_factory=list)
    hp: int = 30
    mana_crystals: int = 0
    mana: int = 0
    # Temporary (coin-style) mana; tracked separately from crystals.
    bonus_mana: int = 0
    fatigue: int = 0
    burned: list[Card] = field(default_factory=list)

    @property
    def available_mana(self) -> int:
        return self.mana + self.bonus_mana


def _deal_damage(system: "CardGameSystem", owner: Player, target: Any, amount: int) -> None:
    """Damage a player or minion; killing a minion queues its deathrattle."""
    if target is None:
        return
    if isinstance(target, Player):
        target.hp -= amount
        return
    if isinstance(target, Card):
        target.health -= amount
        if target.health <= 0 and target in owner.board:
            owner.board.remove(target)
            # Chain: a death queues the minion's deathrattle for resolution.
            system.queue_effect(target, DEATHRATTLE, owner=owner)


def damage_effect(amount: int) -> dict[str, Any]:
    def action(system: "CardGameSystem", owner: Player, source: Card, target: Any) -> None:
        enemy = system.opponent_of(owner)
        _deal_damage(system, enemy, target if target is not None else enemy, amount)

    return {"action": action, "requires_target": True}


def draw_effect(count: int = 1) -> dict[str, Any]:
    def action(system: "CardGameSystem", owner: Player, source: Card, target: Any) -> None:
        system.draw_card(owner, count)

    return {"action": action}


def heal_effect(amount: int) -> dict[str, Any]:
    def action(system: "CardGameSystem", owner: Player, source: Card, target: Any) -> None:
        if isinstance(target, (Player, Card)):
            target.hp += amount

    return {"action": action, "requires_target": True}


class CardGameSystem:
    def __init__(self, players: list[Player] | None = None, seed: int | None = None):
        self.state: dict[str, Any] = {}
        self._rng = random.Random(seed)
        self._effect_queue: list[tuple[Card, str, Any, Player]] = []
        self.resolved_effects: list[tuple[str, str]] = []
        self.log: list[str] = []

        if players is None:
            players = [self._default_player("Player1", seed),
                       self._default_player("Player2", None if seed is None else seed + 1)]
        self.players = players
        for player in self.players:
            self.shuffle_deck(player)

        self.turn_count = 0
        self.current_index = 0
        self.turn_start_time = time.monotonic()

        # First player starts with a crystal and draws an opening card.
        first = self.current_player
        first.mana_crystals = 1
        first.mana = 1
        self.draw_card(first, 1)

    # ------------------------------------------------------------------ setup

    @staticmethod
    def _default_player(name: str, seed: int | None) -> Player:
        cards = [
            Card("River Crocolisk", 2, 2, 3),
            Card("Chillwind Yeti", 4, 4, 5),
            Card("Flame Imp", 1, 3, 2,
                 effects={BATTLECRY: [damage_effect(3)]}),
            Card("Bombadier", 3, 2, 2,
                 effects={BATTLECRY: [damage_effect(2)]}),
            Card("Engineer", 2, 1, 1,
                 effects={BATTLECRY: [draw_effect(1)]}),
            Card("Reckless Bomber", 2, 1, 2,
                 effects={BATTLECRY: [damage_effect(1), damage_effect(1)],
                          DEATHRATTLE: [damage_effect(2)]}),
            Card("Squad Leader", 3, 2, 3,
                 effects={END_OF_TURN: [draw_effect(1)]},
                 requires_board=True),
        ]
        deck = [Card(c.name, c.cost, c.attack, c.health,
                     {timing: [dict(e) for e in effects]
                      for timing, effects in c.effects.items()},
                     c.requires_board)
                for c in cards for _ in range(3)]
        rng = random.Random(seed)
        rng.shuffle(deck)
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
        """Play a card: mana check, board limit and play conditions enforced."""
        player = player or self.current_player

        if card is None:
            card = next((candidate for candidate in player.hand
                         if candidate.cost <= player.available_mana
                         and len(player.board) < MAX_BOARD_SIZE
                         and (not candidate.requires_board or player.board)),
                        None)

        result: dict[str, Any] = {"success": False, "card": None, "reason": None}
        if card is None or card not in player.hand:
            result["reason"] = "no_playable_card"
            return result

        # 1) mana check
        if card.cost > player.available_mana:
            result["reason"] = "insufficient_mana"
            return result

        # 2) board size limit
        if len(player.board) >= MAX_BOARD_SIZE:
            result["reason"] = "board_full"
            return result

        # 3) play condition (e.g. requires another friendly minion)
        if card.requires_board and not player.board:
            result["reason"] = "condition_not_met"
            return result

        # Pay the cost, preferring real crystals over temporary bonus mana.
        paid_bonus = min(player.bonus_mana, card.cost)
        player.bonus_mana -= paid_bonus
        player.mana -= card.cost - paid_bonus

        player.hand.remove(card)
        player.board.append(card)
        self.log.append(f"{player.name} plays {card.name}")

        # Timing: a card entering play only fires its battlecry.
        self.trigger_card_effect(card, BATTLECRY, target=target, owner=player)

        result.update(success=True, card=card)
        return result

    # --------------------------------------------------------------- effects

    def queue_effect(
        self,
        card: Card,
        timing: str,
        target: Any = None,
        owner: Player | None = None,
    ) -> None:
        """Enqueue an effect (used for chains such as deathrattles)."""
        self._effect_queue.append((card, timing, target, owner or self.current_player))

    def trigger_card_effect(
        self,
        card: Card | None = None,
        timing: str = BATTLECRY,
        target: Any = None,
        owner: Player | None = None,
    ) -> list[tuple[str, str]]:
        """Resolve effects for a specific timing, with target choice and chains."""
        if card is not None:
            self.queue_effect(card, timing, target, owner)

        # Drain the queue so effects triggered by other effects are resolved
        # in the same pass (chaining).
        while self._effect_queue:
            source, effect_timing, requested_target, effect_owner = self._effect_queue.pop(0)
            for effect in source.effects.get(effect_timing, []):
                chosen_target = requested_target
                if effect.get("requires_target") and chosen_target is None:
                    chosen_target = self.select_target(effect_owner, effect)
                effect["action"](self, effect_owner, source, chosen_target)
                self.resolved_effects.append((source.name, effect_timing))
        return self.resolved_effects

    def select_target(self, owner: Player, effect: dict[str, Any]) -> Any:
        """Deterministic target choice (never random): enemy minion, then hero."""
        enemy = self.opponent_of(owner)
        if effect.get("targeting") == "friendly":
            return owner.board[0] if owner.board else owner
        if enemy.board:
            # Highest-attack enemy minion is the priority target.
            return max(enemy.board, key=lambda minion: minion.attack)
        return enemy

    # ---------------------------------------------------------------- drawing

    def shuffle_deck(self, player: Player | None = None) -> None:
        """Shuffle the draw pile so its order is not fixed."""
        player = player or self.current_player
        self._rng.shuffle(player.deck)

    def draw_card(self, player: Player | None = None, count: int = 1) -> list[Card]:
        """Draw cards; burn at 10 in hand and take fatigue on an empty deck."""
        player = player or self.current_player
        drawn: list[Card] = []
        for _ in range(count):
            if not player.deck:
                # Fatigue: escalating damage instead of drawing from nothing.
                player.fatigue += 1
                player.hp -= player.fatigue
                self.log.append(f"{player.name} takes {player.fatigue} fatigue damage")
                continue

            card = player.deck.pop(0)
            if len(player.hand) >= MAX_HAND_SIZE:
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
        """Gain permanent crystals (capped at 10) or temporary coin mana."""
        player = player or self.current_player
        if amount <= 0:
            return 0

        if temporary:
            # Coin-style bonus: usable this turn only; never grows crystals.
            player.bonus_mana += amount
            self.log.append(f"{player.name} gains {amount} temporary mana")
            return amount

        # Permanent crystals are hard capped at MAX_MANA_CRYSTALS.
        gained = min(amount, MAX_MANA_CRYSTALS - player.mana_crystals)
        player.mana_crystals += gained
        player.mana = min(player.mana + gained, player.mana_crystals)
        return gained

    # ----------------------------------------------------------------- turns

    def turn_timed_out(self, now: float | None = None) -> bool:
        if now is None:
            now = time.monotonic()
        return now - self.turn_start_time >= TURN_TIME_LIMIT

    def check_timeout(self) -> dict[str, Any] | None:
        """Force the turn to end once the per-turn time budget is exceeded."""
        if self.turn_timed_out():
            return self.end_turn()
        return None

    def end_turn(self) -> dict[str, Any]:
        """Resolve end-of-turn effects, reset mana, then switch the active player."""
        outgoing = self.current_player

        # 1) resolve every end-of-turn effect (and any chains they cause)
        for card in list(outgoing.board):
            self.trigger_card_effect(card, END_OF_TURN, owner=outgoing)

        # 2) reset mana: crystals spent this turn are gone, coin mana expires
        outgoing.mana = 0
        outgoing.bonus_mana = 0
        self.log.append(f"{outgoing.name} ends the turn")

        # 3) switch to the other player
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
