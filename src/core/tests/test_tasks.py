from datetime import timedelta
from mock import patch

from django.core.exceptions import ValidationError
from django.utils import timezone

from djangae.test import TestCase

from core.models import Match, Season, EloHistory, SeasonPlayer
from core.tests.factories import MatchFactory, SeasonFactory, PlayerFactory
from core.tasks import set_active_season, elo_history_migration, season_player_migration
from core.utils import calculate_elo


class SetActiveSeasonTestCase(TestCase):
    """Tests for the task which sets a season as active."""

    def test_expired_active_season_marked_inactive(self):
        today = timezone.now().date()
        yesterday = today - timedelta(days=1)
        earlier = yesterday - timedelta(days=10)
        season = SeasonFactory(
            start_date=earlier, end_date=yesterday, active=True
        )

        set_active_season()

        season.refresh_from_db()
        self.assertFalse(season.active)

    def test_ongoing_season_remains_active(self):
        today = timezone.now().date()
        yesterday = today - timedelta(days=1)
        season = SeasonFactory(start_date=yesterday, active=True)

        set_active_season()

        season.refresh_from_db()
        self.assertTrue(season.active)

    def test_new_season_activated(self):
        today = timezone.now().date()
        season = SeasonFactory(start_date=today)
        self.assertFalse(season.active)

        player = PlayerFactory(
            season_elo = 2000,
            season_win_count = 50,
            season_loss_count = 10,
            season_grannies_given_count = 5,
            season_grannies_taken_count = 2,
        )

        set_active_season()

        season.refresh_from_db()
        self.assertTrue(season.active)

        # as a bi-product of marking a new season as active
        # all season fields on the player instances are reset
        player.refresh_from_db()
        self.assertEqual(player.season_elo, 1000)
        self.assertFalse(player.season_win_count)
        self.assertFalse(player.season_loss_count)
        self.assertFalse(player.season_grannies_given_count)
        self.assertFalse(player.season_grannies_taken_count)


class MigrateEloHistoryTaskTest(TestCase):

    def test_migration_task(self):
        """Tests for the task which generates all historic Elo History instances."""
        season_one, season_two = [SeasonFactory() for x in xrange(2)]
        player_one, player_two = [
            PlayerFactory(
                season_elo = 1000,
                season_win_count = 0,
                season_loss_count = 0,
                season_grannies_given_count = 0,
                season_grannies_taken_count = 0,
            ) for x in xrange(2)
        ]

        # game 1 in season 1
        player_one_match_one_elo, player_two_match_one_elo = calculate_elo(player_one.season_elo, player_two.season_elo)
        match_one = MatchFactory(winner=player_one, loser=player_two, season=season_one, date=season_one.start_date + timedelta(days=1))
        player_one.refresh_from_db()
        player_two.refresh_from_db()

        # game 2 in season 2
        player_one_match_two_elo, player_two_match_two_elo = calculate_elo(player_one.season_elo, player_two.season_elo)
        match_two = MatchFactory(winner=player_one, loser=player_two, season=season_one, date=season_one.start_date + timedelta(days=2))
        player_one.refresh_from_db()
        player_two.refresh_from_db()

        # game 3 in season 1
        player_one.reset_season_fields()

        player_two.reset_season_fields()

        player_one_match_three_elo, player_two_match_three_elo = calculate_elo(player_one.season_elo, player_two.season_elo)
        match_three = MatchFactory(winner=player_one, loser=player_two, season=season_two, date=season_two.start_date + timedelta(days=1))
        player_one.refresh_from_db()
        player_two.refresh_from_db()

        # delete all the elo history instances created in the post save signal
        EloHistory.objects.all().delete()

        # run the task
        elo_history_migration()

        # three games, two elo history for each player
        self.assertEqual(EloHistory.objects.count(), 6)

        match_one_history = EloHistory.objects.get(match=match_one, player=player_one)
        self.assertEqual(match_one_history.elo_score, player_one_match_one_elo)
        match_one_history = EloHistory.objects.get(match=match_one, player=player_two)
        self.assertEqual(match_one_history.elo_score, player_two_match_one_elo)

        match_two_history = EloHistory.objects.get(match=match_two, player=player_one)
        self.assertEqual(match_two_history.elo_score, player_one_match_two_elo)
        match_two_history = EloHistory.objects.get(match=match_two, player=player_two)
        self.assertEqual(match_two_history.elo_score, player_two_match_two_elo)

        match_three_history = EloHistory.objects.get(match=match_three, player=player_one)
        self.assertEqual(match_three_history.elo_score, player_one_match_three_elo)
        match_three_history = EloHistory.objects.get(match=match_three, player=player_two)
        self.assertEqual(match_three_history.elo_score, player_two_match_three_elo)


class MigrateSeasonPlayerTaskTest(TestCase):

    def test_migration_task(self):
        """Tests for the task which generates all historic Season Player instances."""
        season_one, season_two = [SeasonFactory() for x in xrange(2)]
        player_one, player_two = [
            PlayerFactory(
                season_elo = 1000,
                season_win_count = 0,
                season_loss_count = 0,
                season_grannies_given_count = 0,
                season_grannies_taken_count = 0,
            ) for x in xrange(2)
        ]

        # record two games in season one
        match_one = MatchFactory(winner=player_one, loser=player_two, season=season_one, date=season_one.start_date + timedelta(days=1))
        match_two = MatchFactory(winner=player_one, loser=player_two, season=season_one, date=season_one.start_date + timedelta(days=2))

        # cache the season elo and win/loss count
        player_one.refresh_from_db()
        player_one_season_one_elo = player_one.season_elo
        player_one_season_one_win = player_one.season_win_count
        player_one_season_one_loss = player_one.season_loss_count

        player_two.refresh_from_db()
        player_two_season_one_elo = player_two.season_elo
        player_two_season_one_win = player_two.season_win_count
        player_two_season_one_loss = player_two.season_loss_count

        # reset denormalized values before the new season
        player_one.reset_season_fields()
        player_two.reset_season_fields()

        # record a game in season two
        match_three = MatchFactory(winner=player_one, loser=player_two, season=season_two, date=season_two.start_date + timedelta(days=1))

        # cache the season elo and win/loss count
        player_one.refresh_from_db()
        player_one_season_two_elo = player_one.season_elo
        player_one_season_two_win = player_one.season_win_count
        player_one_season_two_loss = player_one.season_loss_count

        player_two.refresh_from_db()
        player_two_season_two_elo = player_two.season_elo
        player_two_season_two_win = player_two.season_win_count
        player_two_season_two_loss = player_two.season_loss_count

        # run the migration task
        season_player_migration()

        season_one_player_one = SeasonPlayer.objects.get(season=season_one, player=player_one)
        season_one_player_two = SeasonPlayer.objects.get(season=season_one, player=player_two)
        season_two_player_one = SeasonPlayer.objects.get(season=season_two, player=player_one)
        season_two_player_two =  SeasonPlayer.objects.get(season=season_two, player=player_two)

        self.assertEqual(season_one_player_one.elo_score, player_one_season_one_elo)
        self.assertEqual(season_one_player_one.win_count, player_one_season_one_win)
        self.assertEqual(season_one_player_one.loss_count, player_one_season_one_loss)

        self.assertEqual(season_one_player_two.elo_score, player_two_season_one_elo)
        self.assertEqual(season_one_player_two.win_count, player_two_season_one_win)
        self.assertEqual(season_one_player_two.loss_count, player_two_season_one_loss)

        self.assertEqual(season_two_player_one.elo_score, player_one_season_two_elo)
        self.assertEqual(season_two_player_one.win_count, player_one_season_two_win)
        self.assertEqual(season_two_player_one.loss_count, player_one_season_two_loss)

        self.assertEqual(season_two_player_two.elo_score, player_two_season_two_elo)
        self.assertEqual(season_two_player_two.win_count, player_two_season_two_win)
        self.assertEqual(season_two_player_two.loss_count, player_two_season_two_loss)
