import unittest

from tabvio.credentials.domains import (
    matches_allowlist,
    normalize_allowlist_entry,
    normalize_hostname,
)


class NormalizeHostnameTests(unittest.TestCase):
    def test_www_is_dropped(self) -> None:
        self.assertEqual("nexmenus.com", normalize_hostname("www.nexmenus.com"))

    def test_a_bare_hostname_is_unchanged(self) -> None:
        self.assertEqual("nexmenus.com", normalize_hostname("nexmenus.com"))

    def test_other_subdomains_are_kept(self) -> None:
        self.assertEqual("login.nexmenus.com", normalize_hostname("login.nexmenus.com"))

    def test_only_the_leading_www_label_is_dropped(self) -> None:
        self.assertEqual("www.example.com", normalize_hostname("www.www.example.com"))

    def test_a_domain_that_is_itself_www_survives(self) -> None:
        """www.com is a registrable domain, not a www prefix."""
        self.assertEqual("www.com", normalize_hostname("www.com"))

    def test_case_trailing_dots_and_urls_are_accepted(self) -> None:
        for value in ("WWW.NexMenus.com.", "https://www.nexmenus.com/", " www.nexmenus.com "):
            with self.subTest(value=value):
                self.assertEqual("nexmenus.com", normalize_hostname(value))

    def test_a_path_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_hostname("nexmenus.com/login")


class NormalizeAllowlistEntryTests(unittest.TestCase):
    def test_a_wildcard_is_kept(self) -> None:
        self.assertEqual("*.nexmenus.com", normalize_allowlist_entry("*.nexmenus.com"))

    def test_a_wildcard_over_a_top_level_domain_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            normalize_allowlist_entry("*.com")

    def test_a_wildcard_over_shared_hosting_is_refused(self) -> None:
        for entry in ("*.github.io", "*.herokuapp.com", "*.vercel.app", "*.co.uk"):
            with self.subTest(entry=entry):
                with self.assertRaises(ValueError):
                    normalize_allowlist_entry(entry)

    def test_a_normal_site_on_a_country_suffix_is_still_allowed(self) -> None:
        self.assertEqual("*.example.co.uk", normalize_allowlist_entry("*.example.co.uk"))


class MatchesAllowlistTests(unittest.TestCase):
    def test_the_reported_redirect_now_matches(self) -> None:
        """Saved for www.nexmenus.com, login redirects to nexmenus.com."""
        stored = [normalize_allowlist_entry("www.nexmenus.com")]

        self.assertTrue(matches_allowlist(normalize_hostname("nexmenus.com"), stored))

    def test_the_redirect_matches_in_the_other_direction(self) -> None:
        stored = [normalize_allowlist_entry("nexmenus.com")]

        self.assertTrue(matches_allowlist(normalize_hostname("www.nexmenus.com"), stored))

    def test_an_exact_entry_does_not_cover_other_subdomains(self) -> None:
        stored = [normalize_allowlist_entry("nexmenus.com")]

        self.assertFalse(matches_allowlist("login.nexmenus.com", stored))

    def test_a_wildcard_covers_subdomains_and_the_domain_itself(self) -> None:
        stored = [normalize_allowlist_entry("*.nexmenus.com")]

        for hostname in ("nexmenus.com", "login.nexmenus.com", "a.b.nexmenus.com"):
            with self.subTest(hostname=hostname):
                self.assertTrue(matches_allowlist(hostname, stored))

    def test_a_wildcard_does_not_leak_to_a_lookalike_domain(self) -> None:
        stored = [normalize_allowlist_entry("*.nexmenus.com")]

        for hostname in ("nexmenus.com.evil.test", "notnexmenus.com", "evil-nexmenus.com"):
            with self.subTest(hostname=hostname):
                self.assertFalse(matches_allowlist(hostname, stored))

    def test_entries_saved_before_these_rules_still_match(self) -> None:
        """Rows written when the allowlist stored www. verbatim."""
        self.assertTrue(matches_allowlist("nexmenus.com", ["www.nexmenus.com"]))

    def test_an_unparseable_stored_entry_is_skipped(self) -> None:
        self.assertTrue(matches_allowlist("nexmenus.com", ["", "nexmenus.com"]))
        self.assertFalse(matches_allowlist("nexmenus.com", [""]))


if __name__ == "__main__":
    unittest.main()
