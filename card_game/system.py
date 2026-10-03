"""
pairwise-card-game-system - CardGameSystem

This module contains a deliberately broken implementation.
Fix all bugs so that tests pass.
"""


class CardGameSystem:
    def __init__(self):
        self.state = {}

    def play_card(self, *args, **kwargs):
        """BUG: 出牌不做费用检查，没费也能出"""
        return None

    def trigger_card_effect(self, *args, **kwargs):
        """BUG: 卡牌效果不做时机，战吼和亡语用同一个"""
        return None

    def draw_card(self, *args, **kwargs):
        """BUG: 抽牌不做疲劳，牌库空了不掉血"""
        return None

    def gain_mana(self, *args, **kwargs):
        """BUG: 费用不做上限，10费以上也能有"""
        return None

    def end_turn(self, *args, **kwargs):
        """BUG: 回合结束不做触发，结束时效果不结算"""
        return None

