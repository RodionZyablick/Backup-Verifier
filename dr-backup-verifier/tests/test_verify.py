import tempfile
import unittest
from pathlib import Path

from tests.fakes import FakeEnv
from verifier.backup import apply_retention, create_backup, list_backups, load_backup, save_manifest
from verifier.config import ConfigError, CustomCheck, load_config
from verifier.docker import Docker
from verifier.drivers import get_driver
from verifier.verify import Verifier, compare_row_counts, evaluate_custom

CONFIG = """
[project]
name = "t"
backup_dir = "./backups"
reports_dir = "./reports"
retention_keep = 2

[source]
type = "postgres"
container = "src"
user = "app"
database = "shop"

[verify]
row_count_tolerance_percent = 0
rto_target_sec = 600

[[verify.custom_checks]]
name = "users not empty"
sql = "SELECT count(*) FROM users"
expect_min = 1
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfgfile = Path(self.tmp.name) / "config.toml"
        cfgfile.write_text(CONFIG)
        self.cfg = load_config(cfgfile)
        self.env = FakeEnv()
        self.env.custom["SELECT count(*) FROM users"] = "100"
        self.driver = get_driver("postgres")
        self.docker = Docker(self.env)

    def verifier(self, **kw):
        return Verifier(self.cfg, self.docker, self.driver, self.env, sleep=lambda s: None, **kw)

    def backup(self):
        return create_backup(self.cfg, self.env, self.driver)


class HappyPath(Base):
    def test_full_cycle_passes_and_cleans_up(self):
        info = self.backup()
        report = self.verifier().verify(info)
        self.assertTrue(report.ok, report.to_text())
        names = [c.name for c in report.checks]
        for expected in ("backup_file_integrity", "provision_isolated_container", "restore",
                         "schema_matches_source", "row_counts_match", "full_data_scan", "cleanup"):
            self.assertIn(expected, names)
        self.assertEqual(self.env.containers, set())
        self.assertEqual(self.env.networks, set())

    def test_isolation_flags(self):
        report = self.verifier().verify(self.backup())
        self.assertTrue(report.ok)
        net = next(c for c in self.env.calls if c[:3] == ["docker", "network", "create"])
        self.assertIn("--internal", net)
        run = next(c for c in self.env.calls if c[:2] == ["docker", "run"])
        self.assertNotIn("-p", run)
        self.assertNotIn("--publish", run)
        self.assertIn("--memory", run)
        self.assertIn("dr-verifier=1", run)
        # пароль не светится в аргументах
        self.assertFalse(any("POSTGRES_PASSWORD=" in x for x in run))


class Failures(Base):
    def test_checksum_mismatch_stops_before_container(self):
        info = self.backup()
        info.path.write_bytes(b"corrupted!")
        report = self.verifier().verify(info)
        self.assertFalse(report.ok)
        self.assertEqual(report.checks[0].name, "backup_file_integrity")
        self.assertFalse(any(c[:2] == ["docker", "run"] for c in self.env.calls))

    def test_restore_failure_detected_and_cleaned(self):
        self.env.restore_rc = 1
        report = self.verifier().verify(self.backup())
        self.assertFalse(report.ok)
        self.assertFalse(next(c for c in report.checks if c.name == "restore").ok)
        self.assertEqual(self.env.containers, set())
        self.assertEqual(self.env.networks, set())

    def test_lost_rows_detected(self):
        self.env.restored_tables["public.orders"] = 450
        report = self.verifier().verify(self.backup())
        c = next(c for c in report.checks if c.name == "row_counts_match")
        self.assertFalse(c.ok)
        self.assertIn("public.orders", c.detail)

    def test_missing_table_detected(self):
        del self.env.restored_tables["public.orders"]
        report = self.verifier().verify(self.backup())
        self.assertFalse(next(c for c in report.checks if c.name == "row_counts_match").ok)

    def test_schema_drift_detected(self):
        self.env.restored_columns = self.env.columns[:-1]
        report = self.verifier().verify(self.backup())
        self.assertFalse(next(c for c in report.checks if c.name == "schema_matches_source").ok)

    def test_invalid_index_detected(self):
        self.env.invalid_indexes = "2"
        report = self.verifier().verify(self.backup())
        self.assertFalse(next(c for c in report.checks if c.name == "нет невалидных индексов").ok)

    def test_scan_failure_detected(self):
        self.env.scan_rc = 1
        report = self.verifier().verify(self.backup())
        self.assertFalse(next(c for c in report.checks if c.name == "full_data_scan").ok)

    def test_custom_check_failure(self):
        self.env.custom["SELECT count(*) FROM users"] = "0"
        report = self.verifier().verify(self.backup())
        self.assertFalse(next(c for c in report.checks if c.name.startswith("custom:")).ok)

    def test_container_dies_on_startup(self):
        self.env.container_alive = False
        report = self.verifier().verify(self.backup())
        self.assertFalse(next(c for c in report.checks if c.name == "provision_isolated_container").ok)
        self.assertEqual(self.env.containers, set())

    def test_startup_timeout(self):
        self.env.ready_lines = 1
        self.cfg.verify.startup_timeout_sec = 3
        ticks = iter(range(0, 1000))
        v = Verifier(self.cfg, self.docker, self.driver, self.env, sleep=lambda s: None, clock=lambda: next(ticks))
        report = v.verify(self.backup())
        self.assertFalse(next(c for c in report.checks if c.name == "provision_isolated_container").ok)

    def test_cleanup_failure_is_reported(self):
        self.env.rm_works = False
        report = self.verifier().verify(self.backup())
        self.assertFalse(report.ok)
        self.assertFalse(next(c for c in report.checks if c.name == "cleanup").ok)

    def test_keep_on_failure_keeps_container(self):
        self.env.restore_rc = 1
        report = self.verifier(keep_on_failure=True).verify(self.backup())
        self.assertTrue(self.env.containers)
        self.assertTrue(report.warnings)

    def test_unexpected_exception_still_cleans_up(self):
        self.env.restore_rc = 0
        original = self.verifier()
        original._restore = lambda info: (_ for _ in ()).throw(RuntimeError("boom"))
        report = original.verify(self.backup())
        self.assertFalse(report.ok)
        self.assertEqual(self.env.containers, set())

    def test_rto_target_exceeded(self):
        self.cfg.verify.rto_target_sec = 5
        t = iter(x * 10 for x in range(1000))   # каждый вызов clock «+10 с»
        v = Verifier(self.cfg, self.docker, self.driver, self.env, sleep=lambda s: None, clock=lambda: next(t))
        report = v.verify(self.backup())
        self.assertFalse(next(c for c in report.checks if c.name == "rto_target").ok)


class Helpers(Base):
    def test_row_count_tolerance(self):
        self.assertEqual(compare_row_counts({"a": 1000}, {"a": 995}, 1.0, 0), [])
        self.assertTrue(compare_row_counts({"a": 1000}, {"a": 980}, 1.0, 0))
        self.assertEqual(compare_row_counts({"a": 10}, {"a": 12}, 0, 5), [])

    def test_custom_eval(self):
        self.assertTrue(evaluate_custom(CustomCheck("x", "", expect_equals="0"), "0.0")[0])
        self.assertFalse(evaluate_custom(CustomCheck("x", "", expect_min=5), "3")[0])
        self.assertTrue(evaluate_custom(CustomCheck("x", "", expect_equals="ok"), "ok")[0])
        self.assertFalse(evaluate_custom(CustomCheck("x", "", expect_max=1), "abc")[0])

    def test_retention_keeps_last_verified(self):
        for i in range(4):
            info = self.backup()
            # у файлов одинаковая секунда — делаем уникальные имена и время вручную
            new = info.path.with_name(f"t-shop-2026010{i}T000000Z.dump")
            info.path.rename(new)
            info.manifest_path.rename(new.with_name(new.name + ".manifest.json"))
            m = load_backup(new)
            m.manifest.created_at = f"2026-01-0{i}T00:00:00+00:00"
            if i == 0:
                m.manifest.last_verification = {"ok": True, "at": "x"}
            save_manifest(m)
        removed = apply_retention(self.cfg.backup_dir, 2)
        left = [b.path.name for b in list_backups(self.cfg.backup_dir)]
        self.assertIn("t-shop-20260100T000000Z.dump", left)   # проверенный защищён
        self.assertEqual(len(removed), 1)

    def test_config_errors(self):
        bad = Path(self.tmp.name) / "bad.toml"
        bad.write_text('[source]\ntype = "oracle"\ndatabase="d"\nuser="u"\n')
        with self.assertRaises(ConfigError):
            load_config(bad)
        with self.assertRaises(ConfigError):
            load_config(Path(self.tmp.name) / "nope.toml")


if __name__ == "__main__":
    unittest.main()
