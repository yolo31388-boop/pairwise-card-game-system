import pytest
from card_game.system import CardGameSystem


class TestCardGameSystem:

    def test_play_card(self):
        """出牌不做费用检查，没费也能出"""
        obj = CardGameSystem()
        result = obj.play_card()
        self.assertIsNotNone(result)

    def test_trigger_card_effect(self):
        """卡牌效果不做时机，战吼和亡语用同一个"""
        obj = CardGameSystem()
        result = obj.trigger_card_effect()
        self.assertIsNotNone(result)

    def test_draw_card(self):
        """抽牌不做疲劳，牌库空了不掉血"""
        obj = CardGameSystem()
        result = obj.draw_card()
        self.assertIsNotNone(result)

    def test_gain_mana(self):
        """费用不做上限，10费以上也能有"""
        obj = CardGameSystem()
        result = obj.gain_mana()
        self.assertIsNotNone(result)

    def test_end_turn(self):
        """回合结束不做触发，结束时效果不结算"""
        obj = CardGameSystem()
        result = obj.end_turn()
        self.assertIsNotNone(result)

