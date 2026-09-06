import unittest
from game_engine import assign_roles, check_win
from database import Player, Game


class GameTests(unittest.TestCase):
    def test_role_assignment_size_and_zombie_present(self):
        ps = [object() for _ in range(6)]
        roles = assign_roles(ps)
        self.assertEqual(len(roles), 6)
        self.assertIn("zombie", roles)

    def test_role_assignment_scales_zombie_count(self):
        ps = [object() for _ in range(18)]
        roles = assign_roles(ps)
        self.assertEqual(len(roles), 18)
        self.assertGreaterEqual(roles.count("zombie"), 2)

    def test_role_assignment_includes_double_agent_when_room(self):
        ps = [object() for _ in range(9)]
        roles = assign_roles(ps)
        self.assertIn("double_agent", roles)

    def test_human_win_no_zombies_left(self):
        g = Game(rescue_eta=6)
        ps = [
            Player(role="zombie", alive=False),
            Player(role="doctor", alive=True),
            Player(role="survivor", alive=True),
        ]
        self.assertEqual(check_win(g, ps), "humans")

    def test_zombie_win_overrun(self):
        g = Game(rescue_eta=6)
        ps = [
            Player(role="zombie", alive=True),
            Player(role="survivor", alive=True),
        ]
        self.assertEqual(check_win(g, ps), "zombie")

    def test_zombie_win_when_rescue_expires(self):
        g = Game(rescue_eta=0)
        ps = [
            Player(role="zombie", alive=True),
            Player(role="doctor", alive=True),
            Player(role="survivor", alive=True),
            Player(role="survivor", alive=True),
        ]
        self.assertEqual(check_win(g, ps), "zombie")

    def test_no_winner_yet(self):
        g = Game(rescue_eta=5)
        ps = [
            Player(role="zombie", alive=True),
            Player(role="doctor", alive=True),
            Player(role="survivor", alive=True),
            Player(role="survivor", alive=True),
        ]
        self.assertIsNone(check_win(g, ps))


if __name__ == "__main__":
    unittest.main()
