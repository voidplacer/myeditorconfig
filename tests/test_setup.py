import base64
import hashlib
import importlib.util
import io
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from editor_setup.common import Backups, Platform, SetupError, run, safe_user_path, version
from editor_setup.config import Configurator
from editor_setup.keyboard import CAPS, ESCAPE, Keyboard, mac_mappings
from editor_setup.menu import Selection, choose, palette, probe, spaced_rows, system_details, viewport
from editor_setup.packages import Packages
from editor_setup.remote import Repository, extract, release_asset


spec = importlib.util.spec_from_file_location("installer", ROOT / "scripts/install.py")
installer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = installer
spec.loader.exec_module(installer)


def result(stdout="", code=0):
    return subprocess.CompletedProcess([], code, stdout, "")


class TemporaryHome(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.environment = patch.dict(os.environ, {"HOME": str(self.home)}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def system(self, kind="ubuntu"):
        if kind == "macos":
            return Platform(system="Darwin", release="26.1", machine="arm64", home=self.home)
        return Platform(system="Linux", os_release={"ID": kind, "VERSION_ID": "24.04"}, machine="x86_64", home=self.home)


class PlatformTests(TemporaryHome):
    @patch("editor_setup.common.subprocess.run", return_value=result())
    def test_command_output_uses_predictable_locale(self, execute):
        with patch.dict(os.environ, {"LC_ALL": "fr_FR.UTF-8"}):
            run(["apt-cache", "policy", "neovim"])
            self.assertEqual(execute.call_args.kwargs["env"]["LC_ALL"], "C")
            run(["brew", "info"], env={"PATH": "/custom/bin", "LC_ALL": "de_DE.UTF-8"})
            self.assertEqual(execute.call_args.kwargs["env"], {"PATH": "/custom/bin", "LC_ALL": "C"})

    def test_supported_platforms(self):
        for kind in ("ubuntu", "fedora", "arch", "macos"):
            self.assertEqual(self.system(kind).kind, kind)

    def test_unsupported_distribution(self):
        with self.assertRaisesRegex(SetupError, "Unsupported Linux"):
            Platform(system="Linux", os_release={"ID": "debian"}, home=self.home)

    def test_old_ubuntu(self):
        with self.assertRaisesRegex(SetupError, "24.04"):
            Platform(system="Linux", os_release={"ID": "ubuntu", "VERSION_ID": "22.04"}, home=self.home)

    def test_old_macos(self):
        with self.assertRaisesRegex(SetupError, "macOS 26"):
            Platform(system="Darwin", release="15.6", home=self.home)

    def test_versions(self):
        self.assertGreater(version("NVIM v0.11.6"), version("0.11.2"))


class BackupTests(TemporaryHome):
    def test_documents_preferred(self):
        (self.home / "Documents").mkdir()
        self.assertEqual(Backups(self.home).root.parent, self.home / "Documents/editor_backup")

    def test_home_fallback(self):
        self.assertEqual(Backups(self.home).root.parent, self.home / "editor_backup")

    def test_localized_documents(self):
        (self.home / "Documents-local").mkdir()
        (self.home / ".config").mkdir()
        (self.home / ".config/user-dirs.dirs").write_text('XDG_DOCUMENTS_DIR="$HOME/Documents-local"\n')
        self.assertEqual(Backups(self.home).root.parent, self.home / "Documents-local/editor_backup")

    def test_backup_restore_and_idempotence(self):
        destination = self.home / ".config/kitty/kitty.conf"
        destination.parent.mkdir(parents=True)
        destination.write_text("old")
        backups = Backups(self.home)
        self.assertTrue(backups.write(destination, "new", "kitty"))
        self.assertFalse(backups.write(destination, "new", "kitty"))
        self.assertEqual(len(backups.records), 1)
        self.assertEqual((backups.root / backups.records[0]["backup"]).read_text(), "old")
        Backups.restore(backups.root / "manifest.json", self.home)
        self.assertEqual(destination.read_text(), "old")

    def test_absent_file_restored_by_removal(self):
        destination = self.home / ".tmux.conf"
        backups = Backups(self.home)
        backups.write(destination, "new", "tmux")
        Backups.restore(backups.root / "manifest.json", self.home)
        self.assertFalse(destination.exists())

    def test_directory_backup(self):
        target = self.home / ".config/nvim"
        target.mkdir(parents=True)
        (target / "init.lua").write_text("original")
        backups = Backups(self.home)
        backups.remove(target, "nvim")
        self.assertFalse(target.exists())
        Backups.restore(backups.root / "manifest.json", self.home)
        self.assertEqual((target / "init.lua").read_text(), "original")

    def test_child_write_cannot_overwrite_original_directory_snapshot(self):
        target = self.home / ".config/nvim"
        target.mkdir(parents=True)
        config = target / "init.lua"
        config.write_text("original")
        backups = Backups(self.home)
        backups.remember(target, "nvim")
        config.write_text("starter")
        backups.write(config, "overlay", "nvim")
        backups.write(target / "new.lua", "new overlay", "nvim")
        self.assertEqual(len(backups.records), 1)
        Backups.restore(backups.root / "manifest.json", self.home)
        self.assertEqual(config.read_text(), "original")
        self.assertFalse((target / "new.lua").exists())

    def test_absent_directory_record_covers_new_children(self):
        target = self.home / ".config/nvim"
        backups = Backups(self.home)
        backups.remember(target, "nvim")
        backups.write(target / "init.lua", "new", "nvim")
        self.assertEqual(len(backups.records), 1)
        Backups.restore(backups.root / "manifest.json", self.home)
        self.assertFalse(target.exists())

    def test_refuse_destination_symlink(self):
        (self.home / "linked").symlink_to("/tmp", target_is_directory=True)
        with self.assertRaisesRegex(SetupError, "symlink"):
            safe_user_path(self.home / "linked/config", self.home)

    def test_refuse_backup_directory_symlink(self):
        (self.home / "editor_backup").symlink_to("/tmp", target_is_directory=True)
        with self.assertRaisesRegex(SetupError, "symlink"):
            Backups(self.home).write(self.home / "config", "new", "kitty")

    def test_private_backup_directory(self):
        backups = Backups(self.home)
        backups.write(self.home / "config", "value", "kitty")
        self.assertEqual(backups.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((backups.root / "manifest.json").stat().st_mode & 0o777, 0o600)

    def test_restore_validates_all_records_before_deleting(self):
        target = self.home / "config"
        target.write_text("keep")
        manifest = self.home / "manifest.json"
        manifest.write_text(json.dumps({"home": str(self.home), "records": [{"path": str(target), "backup": None, "kind": "absent"}, {"path": "/etc/passwd", "backup": None, "kind": "absent"}]}))
        with self.assertRaises(SetupError):
            Backups.restore(manifest, self.home)
        self.assertEqual(target.read_text(), "keep")


class RepositoryTests(TemporaryHome):
    def test_selective_staging(self):
        network = Mock()
        network.json.return_value = {"tree": [{"type": "blob", "path": "configs/kitty/kitty.conf"}, {"type": "blob", "path": "configs/tmux/tmux.conf"}]}
        repository = Repository(remote="owner/configs", ref="a" * 40, network=network)
        repository.stage("configs/kitty", self.home / "stage")
        self.assertEqual(network.download.call_count, 1)
        self.assertIn("/" + "a" * 40 + "/configs/kitty/kitty.conf", network.download.call_args.args[0])
        self.assertNotIn("tmux", network.download.call_args.args[0])

    def test_missing_prefix_is_error(self):
        repository = Repository(ROOT)
        with self.assertRaisesRegex(SetupError, "No files"):
            repository.stage("configs/does-not-exist", self.home / "stage")

    def test_repository_symlink_is_refused(self):
        source = self.home / "repo"
        source.mkdir()
        (source / "private").symlink_to("/etc/passwd")
        with self.assertRaises(SetupError):
            Repository(source).get("private", self.home / "copied")

    def test_truncated_tree_is_error(self):
        network = Mock()
        network.json.return_value = {"truncated": True, "tree": []}
        with self.assertRaisesRegex(SetupError, "truncated"):
            Repository(remote="owner/repo", ref="a" * 40, network=network)

    def test_tar_traversal_refused(self):
        archive = self.home / "bad.tar"
        with tarfile.open(archive, "w") as bundle:
            item = tarfile.TarInfo("../outside")
            item.size = 3
            bundle.addfile(item, io.BytesIO(b"bad"))
        with self.assertRaises(SetupError):
            extract(archive, self.home / "unpacked")

    def test_tar_escaping_link_refused(self):
        archive = self.home / "bad.tar"
        with tarfile.open(archive, "w") as bundle:
            item = tarfile.TarInfo("bad-link")
            item.type = tarfile.SYMTYPE
            item.linkname = "../../etc"
            bundle.addfile(item)
        with self.assertRaises(SetupError):
            extract(archive, self.home / "unpacked")

    def test_release_asset_requires_checksum(self):
        network = Mock()
        for digest in (None, "", "sha256:", "sha256:not-a-checksum", 123):
            network.json.return_value = {"assets": [{"name": "font.tar.xz", "digest": digest}]}
            with self.subTest(digest=digest), self.assertRaisesRegex(SetupError, "digest"):
                release_asset(network, "owner/repo", "font.tar.xz")

    def test_damaged_archive_reports_setup_error(self):
        archive = self.home / "damaged.tar.gz"
        archive.write_bytes(b"not an archive")
        with self.assertRaisesRegex(SetupError, "damaged archive"):
            extract(archive, self.home / "unpacked")

    def test_new_bundled_font_auto_discovered(self):
        repository = self.home / "repo"
        (repository / "fonts/new-font").mkdir(parents=True)
        (repository / "fonts/new-font/font.ttf").write_bytes(b"test font")
        (repository / "catalog.json").write_text('{"schema":1,"applications":[]}')
        keyboard = Mock()
        keyboard.status.return_value = ("OFF", None)
        entries = installer.entries_for(Repository(repository), keyboard, self.system())
        self.assertEqual(entries[0]["id"], "font-new-font")
        self.assertNotIn("unsupported", entries[0])
        entries = installer.entries_for(Repository(repository), keyboard, self.system("macos"))
        self.assertIn("unsupported", entries[0])


class PackageTests(TemporaryHome):
    @patch("editor_setup.packages.shutil.which", return_value="/usr/bin/tool")
    @patch("editor_setup.packages.run")
    def test_lazyvim_optional_prerequisites_are_not_installed_by_default(self, execute, locate):
        def response(arguments, **kwargs):
            if arguments[:2] == ["nvim", "--version"]:
                return result("NVIM v0.11.4\nLuaJIT 2.1")
            if arguments[:2] == ["git", "--version"]:
                return result("git version 2.50.0")
            if arguments[:2] == ["fzf", "--version"]:
                return result("0.60.0")
            return result("/opt/homebrew")
        execute.side_effect = response
        java = {"ubuntu": "openjdk-21-jdk", "fedora": "java-21-openjdk-devel", "arch": "jdk21-openjdk", "macos": "openjdk@21"}
        for kind, package in java.items():
            with self.subTest(kind=kind), patch.dict(os.environ, {"PATH": "/usr/bin"}):
                packages = self.packages(kind)
                packages.refresh = Mock()
                packages.check_existing = Mock()
                packages.available = Mock(return_value="0.11.4")
                packages.install = Mock()
                packages.lazyvim()
                self.assertNotIn(package, packages.install.call_args.args[0])
                if kind == "ubuntu":
                    self.assertNotIn("clangd", packages.install.call_args.args[0])
                packages.lazyvim([package])
                self.assertIn(package, packages.install.call_args.args[0])

    def packages(self, kind):
        packages = Packages(self.system(kind), Mock(), Backups(self.home), assume_yes=True)
        packages.sudo = Mock(return_value=result())
        return packages

    @patch("editor_setup.packages.run")
    def test_apt_installs_latest_candidate_even_when_installed(self, execute):
        execute.return_value = result("  Candidate: 0.40.0-1\n")
        packages = self.packages("ubuntu")
        packages.install(["kitty"])
        self.assertEqual(packages.sudo.call_args_list[0].args[0], ["apt-get", "update", "--error-on=any"])
        self.assertIn("kitty", packages.sudo.call_args_list[1].args[0])

    @patch("editor_setup.packages.run")
    def test_fedora_explicitly_upgrades(self, execute):
        execute.return_value = result("0.40.0\n")
        packages = self.packages("fedora")
        packages.install(["kitty"])
        self.assertIn(["dnf", "upgrade", "-y", "kitty"], [call.args[0] for call in packages.sudo.call_args_list])

    @patch("editor_setup.packages.run")
    def test_arch_full_upgrade_not_partial_sync(self, execute):
        execute.return_value = result("Version : 0.40.0-1\n")
        packages = self.packages("arch")
        packages.install(["kitty"])
        commands = [call.args[0] for call in packages.sudo.call_args_list]
        self.assertIn(["pacman", "-Syu"], commands)
        self.assertNotIn(["pacman", "-Sy"], commands)

    def test_unsupported_lazyvim_never_installs(self):
        packages = self.packages("ubuntu")
        packages.refresh = Mock()
        packages.check_existing = Mock()
        packages.available = Mock(return_value="0.9.5-1")
        packages.install = Mock()
        with self.assertRaisesRegex(SetupError, "No upstream fallback"):
            packages.lazyvim()
        packages.install.assert_not_called()

    @patch("editor_setup.packages.run")
    def test_brew_formula_is_bottle_only(self, execute):
        def response(arguments, **kwargs):
            if arguments[1] == "deps":
                return result("")
            if arguments[1] == "info":
                return result(json.dumps({"formulae": [{"name": "tmux", "versions": {"stable": "3.5"}, "bottle": {"stable": {"files": {"arm64_tahoe": {}}}}}]}))
            if arguments[1] == "list":
                return result(code=1)
            return result()
        execute.side_effect = response
        packages = self.packages("macos")
        packages.install(["tmux"])
        commands = [call.args[0] for call in execute.call_args_list]
        self.assertIn(["brew", "fetch", "--force-bottle", "--deps", "tmux"], commands)
        self.assertIn(["brew", "install", "--formula", "--force-bottle", "--ignore-dependencies", "tmux"], commands)

    @patch("editor_setup.packages.run")
    def test_brew_unbottled_formula_refused(self, execute):
        execute.side_effect = [result(""), result('{"formulae":[{"name":"source-only","bottle":{}}]}')]
        with self.assertRaisesRegex(SetupError, "no macOS bottle"):
            self.packages("macos").bottles("source-only")

    def test_aur_source_package_refused(self):
        with self.assertRaisesRegex(SetupError, "Unapproved"):
            self.packages("arch").aur_recipe("compile-this")

    def test_aur_build_function_refused(self):
        packages = self.packages("arch")
        def download(url, target):
            target.write_text("build() { go build; }" if "PKGBUILD?" in url else "depends = git")
            return target
        packages.network.download.side_effect = download
        with self.assertRaisesRegex(SetupError, "compile"):
            packages.aur_recipe("example-bin")

    def test_vendor_repo_never_overwrites_existing_system_file(self):
        target = self.home / "vscode.sources"
        target.write_text("existing")
        packages = self.packages("ubuntu")
        with self.assertRaisesRegex(SetupError, "overwrite"):
            packages.system_file(target, b"different", self.home)
        packages.sudo.assert_not_called()

    @patch("editor_setup.packages.shutil.which")
    def test_snap_installation_never_duplicated(self, locate):
        locate.return_value = "/snap/bin/code"
        with self.assertRaisesRegex(SetupError, "Snap"):
            self.packages("ubuntu").check_existing({"id": "vscode", "name": "VS Code"}, "code")

    @patch("editor_setup.packages.run")
    @patch("editor_setup.packages.shutil.which")
    def test_flatpak_installation_never_duplicated(self, locate, execute):
        locate.side_effect = lambda name: "/usr/bin/flatpak" if name == "flatpak" else None
        execute.return_value = result()
        with self.assertRaisesRegex(SetupError, "Flatpak"):
            self.packages("ubuntu").check_existing({"id": "vscode", "name": "VS Code"}, "code")

    @patch("editor_setup.packages.run")
    def test_brew_dependencies_forced_to_bottles_individually(self, execute):
        def response(arguments, **kwargs):
            if arguments[1] == "deps":
                return result("dependency\n")
            if arguments[1] == "info":
                return result(json.dumps({"formulae": [{"name": "tmux", "versions": {"stable": "3.5"}, "bottle": {"stable": {"files": {"arm64_tahoe": {}}}}}]}))
            return result()
        execute.side_effect = response
        packages = self.packages("macos")
        packages.install(["tmux"])
        commands = [call.args[0] for call in execute.call_args_list]
        self.assertIn(["brew", "install", "--formula", "--force-bottle", "--ignore-dependencies", "dependency"], commands)
        self.assertEqual(packages.env["HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK"], "1")


class JdkTests(TemporaryHome):
    @patch("editor_setup.packages.Path.glob")
    @patch("editor_setup.packages.shutil.which")
    @patch("editor_setup.packages.subprocess.run")
    def test_homebrew_jdk_uses_stable_opt_path_after_upgrades(self, execute, locate, glob):
        real_home = self.home / "Cellar/openjdk@25/25.0.1/libexec/openjdk.jdk/Contents/Home"
        (real_home / "bin").mkdir(parents=True)
        (real_home / "bin/javac").touch()
        (real_home / "bin/java").touch()
        formula = self.home / "opt/openjdk@25"
        formula.parent.mkdir()
        formula.symlink_to(self.home / "Cellar/openjdk@25/25.0.1")
        stable_home = formula / "libexec/openjdk.jdk/Contents/Home"
        locate.return_value = str(real_home / "bin/javac")
        glob.return_value = [stable_home / "bin/javac"]
        execute.side_effect = [result("javac 25.0.1"), result('openjdk version "25.0.1"')]
        packages = Packages(self.system("macos"), Mock(), Backups(self.home))
        self.assertEqual(packages.installed_jdks(), [(25, stable_home)])

    def packages(self, kind="ubuntu", installed=()):
        packages = Packages(self.system(kind), Mock(), Backups(self.home), assume_yes=True)
        packages.network.json.return_value = {"most_recent_lts": 25, "available_lts_releases": [8, 11, 17, 21, 25]}
        packages.installed_jdks = Mock(return_value=list(installed))
        packages.refresh = Mock()
        packages.available = Mock(return_value=None)
        packages.install = Mock()
        return packages

    def test_equal_or_newer_jdk_is_reused_without_package_operations(self):
        for release in (25, 26, 27):
            with self.subTest(release=release):
                packages = self.packages(installed=[(release, self.home / "jdk")])
                self.assertEqual(packages.ensure_jdk(), self.home / "jdk")
                packages.install.assert_not_called()
                packages.refresh.assert_not_called()
                packages.available.assert_not_called()

    def test_older_jdk_installs_newest_repository_lts_on_each_platform(self):
        choices = {"ubuntu": "openjdk-25-jdk", "fedora": "java-25-openjdk-devel", "arch": "jdk25-openjdk", "macos": "openjdk@25"}
        for kind, package in choices.items():
            with self.subTest(kind=kind):
                packages = self.packages(kind)
                packages.installed_jdks.side_effect = [[(17, self.home / "old")], [(25, self.home / "new")]]
                packages.available.side_effect = lambda name: "25.0.1" if name == package else None
                self.assertEqual(packages.ensure_jdk(), self.home / "new")
                packages.install.assert_called_once_with([package])

    def test_newer_than_repository_lts_is_not_downgraded(self):
        packages = self.packages(installed=[(22, self.home / "jdk22")])
        packages.available.side_effect = lambda name: "21.0.9" if name == "openjdk-21-jdk" else None
        self.assertEqual(packages.ensure_jdk(), self.home / "jdk22")
        packages.install.assert_not_called()

    def test_latest_available_lts_is_used_when_newest_lts_is_missing(self):
        packages = self.packages()
        packages.available.side_effect = lambda name: "21.0.9" if name == "openjdk-21-jdk" else None
        packages.installed_jdks.side_effect = [[], [(21, self.home / "jdk21")]]
        self.assertEqual(packages.ensure_jdk(), self.home / "jdk21")
        packages.install.assert_called_once_with(["openjdk-21-jdk"])

    def test_future_lts_is_discovered_from_metadata_not_hardcoded(self):
        packages = self.packages()
        packages.network.json.return_value = {"most_recent_lts": 29, "available_lts_releases": [21, 25, 29]}
        packages.available.side_effect = lambda name: "29.0.1" if name == "openjdk-29-jdk" else None
        packages.installed_jdks.side_effect = [[], [(29, self.home / "jdk29")]]
        self.assertEqual(packages.ensure_jdk(), self.home / "jdk29")
        packages.install.assert_called_once_with(["openjdk-29-jdk"])

    def test_unversioned_non_lts_package_is_not_selected(self):
        packages = self.packages("arch")
        packages.available.side_effect = lambda name: {"jdk-openjdk": "27.0.1", "jdk21-openjdk": "21.0.9"}.get(name)
        packages.installed_jdks.side_effect = [[], [(21, self.home / "jdk21")]]
        packages.ensure_jdk()
        packages.install.assert_called_once_with(["jdk21-openjdk"])

    def test_missing_metadata_or_repository_support_never_installs_unmanaged_jdk(self):
        packages = self.packages()
        packages.network.json.return_value = {}
        with self.assertRaisesRegex(SetupError, "official metadata"):
            packages.ensure_jdk()
        packages.install.assert_not_called()
        packages.network.json.return_value = {"most_recent_lts": 25, "available_lts_releases": [21, 25]}
        with self.assertRaisesRegex(SetupError, "No upstream binary fallback"):
            packages.ensure_jdk()
        packages.install.assert_not_called()

    def test_installed_compatible_jdk_is_kept_when_repo_has_no_supported_lts(self):
        packages = self.packages(installed=[(22, self.home / "jdk22")])
        self.assertEqual(packages.ensure_jdk(), self.home / "jdk22")
        packages.install.assert_not_called()

    def test_jdk_version_parser(self):
        for text, expected in (("javac 25.0.1", 25), ('openjdk version "21.0.9"', 21), ('java version "1.8.0_452"', 8), ("25+36", 25), ("no version", 0)):
            with self.subTest(text=text):
                self.assertEqual(Packages.jdk_major(text), expected)

    @patch("editor_setup.packages.Path.glob", return_value=[])
    @patch("editor_setup.packages.shutil.which", return_value=None)
    @patch("editor_setup.packages.subprocess.run")
    def test_detection_requires_both_java_and_javac(self, execute, locate, glob):
        packages = Packages(self.system(), Mock(), Backups(self.home))
        java_home = self.home / "java"
        (java_home / "bin").mkdir(parents=True)
        (java_home / "bin/java").touch()
        with patch.dict(os.environ, {"JAVA_HOME": str(java_home)}):
            self.assertEqual(packages.installed_jdks(), [])
            execute.assert_not_called()
            (java_home / "bin/javac").touch()
            execute.side_effect = [result("javac 25.0.1"), result('openjdk version "25.0.1"')]
            self.assertEqual(packages.installed_jdks(), [(25, java_home)])


class ConfigurationTests(TemporaryHome):
    @patch("editor_setup.config.run")
    def test_fresh_lazyvim_replacement_restores_original_directory(self, execute):
        destination = self.home / ".config/nvim"
        (destination / "lua/config").mkdir(parents=True)
        keymaps = destination / "lua/config/keymaps.lua"
        keymaps.write_text("original keys")
        (destination / "init.lua").write_text("original init")

        def clone(arguments, **kwargs):
            starter = Path(arguments[-1])
            (starter / ".git").mkdir(parents=True)
            (starter / "lua/config").mkdir(parents=True)
            (starter / "init.lua").write_text("starter init")
            (starter / "lua/config/keymaps.lua").write_text("starter keys")
            return result()

        execute.side_effect = clone
        backups = Backups(self.home)
        configurator = Configurator(self.system(), Repository(ROOT), Mock(), backups)
        configurator.nvim(ROOT / "configs/nvim")
        self.assertEqual(keymaps.read_bytes(), (ROOT / "configs/nvim/lua/config/keymaps.lua").read_bytes())
        Backups.restore(backups.root / "manifest.json", self.home)
        self.assertEqual(keymaps.read_text(), "original keys")
        self.assertEqual((destination / "init.lua").read_text(), "original init")
        self.assertFalse((destination / "lazyvim.json").exists())

    @patch("editor_setup.config.run", side_effect=SetupError("Clone failed"))
    def test_failed_starter_download_preserves_existing_neovim(self, execute):
        destination = self.home / ".config/nvim"
        destination.mkdir(parents=True)
        original = destination / "init.lua"
        original.write_text("personal configuration")
        data = self.home / ".local/share/nvim"
        data.mkdir(parents=True)
        (data / "keep").write_text("personal data")
        backups = Backups(self.home)
        configurator = Configurator(self.system(), Repository(ROOT), Mock(), backups)
        with self.assertRaisesRegex(SetupError, "Clone failed"):
            configurator.nvim(ROOT / "configs/nvim")
        self.assertEqual(original.read_text(), "personal configuration")
        self.assertEqual((data / "keep").read_text(), "personal data")
        self.assertEqual(backups.records, [])

    @patch("editor_setup.config.run")
    def test_unsafe_neovim_data_path_is_rejected_before_download_or_removal(self, execute):
        destination = self.home / ".config/nvim"
        destination.mkdir(parents=True)
        (destination / "init.lua").write_text("keep")
        (self.home / ".cache").symlink_to("/tmp", target_is_directory=True)
        configurator = Configurator(self.system(), Repository(ROOT), Mock(), Backups(self.home))
        with self.assertRaisesRegex(SetupError, "symlink"):
            configurator.nvim(ROOT / "configs/nvim")
        execute.assert_not_called()
        self.assertEqual((destination / "init.lua").read_text(), "keep")

    def test_neovim_java_runtime_supports_unicode_paths(self):
        destination = self.home / ".config/nvim"
        (destination / "lua/config").mkdir(parents=True)
        (destination / "lua/config/lazy.lua").write_text('return { "LazyVim/LazyVim" }')
        java_home = str(self.home / "বাংলা/JDK 25")
        configurator = Configurator(self.system(), Repository(ROOT), Mock(), Backups(self.home))
        configurator.nvim(ROOT / "configs/nvim", ["lang.java"], java_home)
        options = destination / "lua/config/options.lua"
        self.assertIn(java_home, options.read_text())
        self.assertNotIn("\\u", options.read_text())
        if shutil.which("nvim"):
            completed = subprocess.run(["nvim", "--clean", "--headless", "-u", "NONE", "+lua dofile(" + json.dumps(str(options)) + ")", "+qa"], check=True, capture_output=True, timeout=15, env=dict(os.environ, PATH=os.defpath))
            self.assertEqual(completed.stderr, b"")

    def test_selected_java_home_is_scoped_to_neovim_and_idempotent(self):
        destination = self.home / ".config/nvim"
        (destination / "lua/config").mkdir(parents=True)
        (destination / "lua/config/lazy.lua").write_text('return { "LazyVim/LazyVim" }')
        options = destination / "lua/config/options.lua"
        options.write_text("vim.opt.number = false\n")
        entry = {"id": "nvim", "handler": "nvim", "extras": ["lang.java"], "java_home": str(self.home / "JDK 25")}
        configurator = Configurator(self.system(), Repository(ROOT), Mock(), Backups(self.home))
        configurator.apply(entry, ROOT / "configs/nvim")
        first = options.read_text()
        self.assertIn("vim.opt.number = false", first)
        self.assertIn('vim.env.JAVA_HOME = ' + json.dumps(entry["java_home"]), first)
        self.assertIn(str(self.home / "JDK 25/bin") + ":", first)
        self.assertNotIn("JAVA_HOME", os.environ)
        configurator.apply(entry, ROOT / "configs/nvim")
        self.assertEqual(options.read_text(), first)
        self.assertEqual(first.count("-- BEGIN myeditorconfig Java runtime"), 1)

    def test_neovim_with_no_extras_writes_no_language_imports(self):
        destination = self.home / ".config/nvim"
        (destination / "lua/config").mkdir(parents=True)
        (destination / "lua/config/lazy.lua").write_text('return { "LazyVim/LazyVim" }')
        configurator = Configurator(self.system(), Repository(ROOT), Mock(), Backups(self.home))
        configurator.apply({"id": "nvim", "handler": "nvim"}, ROOT / "configs/nvim")
        state = json.loads((destination / "lazyvim.json").read_text())
        self.assertEqual(state, {"extras": [], "version": 1})
        configurator.nvim(ROOT / "configs/nvim")
        self.assertEqual(json.loads((destination / "lazyvim.json").read_text()), state)

    def test_neovim_enables_only_requested_extras_and_preserves_existing(self):
        destination = self.home / ".config/nvim"
        (destination / "lua/config").mkdir(parents=True)
        (destination / "lua/config/lazy.lua").write_text('return { "LazyVim/LazyVim" }')
        target = destination / "lazyvim.json"
        target.write_text('{"extras":["personal.extra"],"version":8}')
        configurator = Configurator(self.system(), Repository(ROOT), Mock(), Backups(self.home))
        configurator.apply({"id": "nvim", "handler": "nvim", "extras": ["lang.clangd"]}, ROOT / "configs/nvim")
        self.assertEqual(json.loads(target.read_text())["extras"], ["lazyvim.plugins.extras.lang.clangd", "personal.extra"])
        configurator.apply({"id": "nvim", "handler": "nvim", "extras": ["lang.java"]}, ROOT / "configs/nvim")
        self.assertEqual(json.loads(target.read_text())["extras"], ["lazyvim.plugins.extras.lang.clangd", "lazyvim.plugins.extras.lang.java", "personal.extra"])

    def test_neovim_rerun_repairs_and_backs_up_damaged_extras(self):
        destination = self.home / ".config/nvim"
        (destination / "lua/config").mkdir(parents=True)
        (destination / "lua/config/lazy.lua").write_text('return { "LazyVim/LazyVim" }')
        target = destination / "lazyvim.json"
        original = {"extras": ["lazyvim.plugins.extras.lazyvim.plugins.extras.lang.java"], "version": 8, "news": {"NEWS.md": "11866"}}
        target.write_text(json.dumps(original))
        backups = Backups(self.home)
        configurator = Configurator(self.system(), Repository(ROOT), Mock(), backups)
        configurator.nvim(ROOT / "configs/nvim")
        actual = json.loads(target.read_text())
        self.assertEqual(actual["extras"], ["lazyvim.plugins.extras.lang.java"])
        self.assertEqual(actual["news"], original["news"])
        saved = next(record for record in backups.records if record["path"] == str(target))
        self.assertEqual(json.loads((backups.root / saved["backup"]).read_text()), original)
        first = target.read_bytes()
        configurator.nvim(ROOT / "configs/nvim")
        self.assertEqual(target.read_bytes(), first)

    def test_fresh_lazyvim_state_declares_full_module_name_schema(self):
        state = Configurator.lazyvim_state({"extras": []}, ["lang.clangd", "lang.java"])
        self.assertEqual(state, {"version": 1, "extras": ["lazyvim.plugins.extras.lang.clangd", "lazyvim.plugins.extras.lang.java"]})

    def test_legacy_lazyvim_state_normalizes_names_before_migration(self):
        state = Configurator.lazyvim_state({"extras": ["lang.python", "lazyvim.plugins.extras.lang.java"]}, ["lang.java"])
        self.assertEqual(state["version"], 1)
        self.assertEqual(state["extras"], ["lazyvim.plugins.extras.lang.java", "lazyvim.plugins.extras.lang.python"])

    def test_damaged_lazyvim_state_is_repaired_without_losing_metadata(self):
        prefix = "lazyvim.plugins.extras."
        original = {"extras": [prefix * 2 + "lang.clangd", prefix * 3 + "lang.java", prefix + "lang.java", "personal.extra"], "version": 8, "news": {"NEWS.md": "11866"}, "install_version": 7}
        state = Configurator.lazyvim_state(original, ["lang.clangd", prefix + "lang.java"])
        self.assertEqual(state["extras"], [prefix + "lang.clangd", prefix + "lang.java", "personal.extra"])
        self.assertEqual(state["version"], 8)
        self.assertEqual(state["news"], original["news"])
        self.assertEqual(state["install_version"], 7)
        self.assertEqual(Configurator.lazyvim_state(state, ["lang.clangd", "lang.java"]), state)
        self.assertEqual(original["extras"][0], prefix * 2 + "lang.clangd")

    def test_invalid_lazyvim_state_is_rejected(self):
        for state, extras in (([], []), ({"extras": "lang.java"}, []), ({"extras": [None]}, []), ({}, ["lazyvim.plugins.extras."]), ({}, ["lang/../../bad"])):
            with self.subTest(state=state, extras=extras), self.assertRaises(SetupError):
                Configurator.lazyvim_state(state, extras)

    def test_invalid_lazyvim_schema_version_is_rejected(self):
        for schema in ("8", True, -1, [], {}):
            with self.subTest(schema=schema), self.assertRaisesRegex(SetupError, "version"):
                Configurator.lazyvim_state({"extras": [], "version": schema}, [])

    def test_formatting_styles_apply_independently_and_backup_existing_style(self):
        repository = Repository(ROOT)
        entries = {entry["id"]: entry for entry in repository.read_json("catalog.json")["applications"]}
        for identity in ("formatting", "formatting-allman"):
            with self.subTest(identity=identity):
                target = self.home / ".clang-format"
                target.write_text("Original style\n")
                backups = Backups(self.home)
                configurator = Configurator(self.system(), repository, Mock(), backups)
                entry = entries[identity]
                stage = self.home / identity
                repository.stage(entry["prefix"], stage)
                configurator.apply(entry, stage)
                self.assertEqual(target.read_bytes(), (ROOT / entry["prefix"] / ".clang-format").read_bytes())
                saved = next(record for record in backups.records if record["path"] == str(target))
                self.assertEqual((backups.root / saved["backup"]).read_text(), "Original style\n")
                self.assertFalse((self.home / ".config/nvim").exists())

    def test_explicit_formatting_conflicts_are_rejected_before_application(self):
        entries = json.loads((ROOT / "catalog.json").read_text())["applications"]
        selected = [entry for entry in entries if entry["id"] in ("formatting", "formatting-allman")]
        for choices in (selected, selected[::-1]):
            with self.assertRaisesRegex(SetupError, "Choose only one clang-format style"):
                installer.validate_selection(choices)

    def shell_configurator(self):
        packages = Mock()
        backups = Backups(self.home)
        return Configurator(self.system(), Repository(ROOT), packages, backups)

    def test_shell_append_preserves_content_and_backs_up(self):
        for shell in ("bash", "zsh"):
            with self.subTest(shell=shell):
                target = self.home / f".{shell}rc"
                original = 'export PERSONAL_SETTING="keep me"\n'
                target.write_text(original)
                target.chmod(0o640)
                configurator = self.shell_configurator()
                configurator.apply({"id": shell, "handler": "shell"}, ROOT / "configs/shell")
                installed = target.read_text()
                self.assertTrue(installed.startswith(original))
                self.assertTrue(installed.endswith(f'eval "$(zoxide init --cmd cd {shell})"\n# END myeditorconfig shell profile\n'))
                self.assertNotIn("oh-my-zsh.sh", installed)
                self.assertEqual(target.stat().st_mode & 0o777, 0o640)
                configurator.packages.install.assert_called_once_with(["zoxide", "eza"])
                record = configurator.backups.records[0]
                self.assertEqual((configurator.backups.root / record["backup"]).read_text(), original)

    def test_shell_rerun_is_idempotent_and_refreshes_only_block(self):
        for shell in ("bash", "zsh"):
            with self.subTest(shell=shell):
                configurator = self.shell_configurator()
                entry = {"id": shell, "handler": "shell"}
                target = self.home / f".{shell}rc"
                configurator.apply(entry, ROOT / "configs/shell")
                original = target.read_text()
                configurator.apply(entry, ROOT / "configs/shell")
                self.assertEqual(target.read_text(), original)
                target.write_text(original.replace("alias restartblue=", "alias old_restartblue=") + "export AFTER_BLOCK=keep\n")
                configurator.apply(entry, ROOT / "configs/shell")
                installed = target.read_text()
                self.assertTrue(installed.startswith("export AFTER_BLOCK=keep\n"))
                self.assertNotIn("old_restartblue", installed)
                self.assertEqual(installed.count("# BEGIN myeditorconfig shell profile"), 1)

    def test_shell_append_handles_missing_final_newline(self):
        target = self.home / ".bashrc"
        target.write_bytes(b"export KEEP=yes")
        self.shell_configurator().apply({"id": "bash", "handler": "shell"}, ROOT / "configs/shell")
        self.assertTrue(target.read_text().startswith("export KEEP=yes\n# BEGIN"))

    def test_shell_bad_markers_leave_file_and_dependencies_untouched(self):
        target = self.home / ".zshrc"
        for original in ("# BEGIN myeditorconfig shell profile\n", "# END myeditorconfig shell profile\n# BEGIN myeditorconfig shell profile\n"):
            target.write_text(original)
            configurator = self.shell_configurator()
            with self.assertRaisesRegex(SetupError, "Malformed shell"):
                configurator.apply({"id": "zsh", "handler": "shell"}, ROOT / "configs/shell")
            self.assertEqual(target.read_text(), original)
            configurator.packages.install.assert_not_called()

    def test_shell_dependency_failure_preserves_existing_file(self):
        target = self.home / ".bashrc"
        target.write_text("export KEEP=yes\n")
        configurator = self.shell_configurator()
        configurator.packages.install.side_effect = SetupError("Unsupported dependency")
        with self.assertRaisesRegex(SetupError, "Unsupported dependency"):
            configurator.apply({"id": "bash", "handler": "shell"}, ROOT / "configs/shell")
        self.assertEqual(target.read_text(), "export KEEP=yes\n")

    def test_shared_shell_profile_syntax(self):
        for shell in ("bash", "zsh"):
            executable = shutil.which(shell)
            if executable:
                subprocess.run([executable, "-n", str(ROOT / "configs/shell/profile.sh")], check=True, capture_output=True)

    def test_configuration_formats_validate(self):
        Configurator.validate(ROOT / "configs")

    def test_alacritty_uses_native_shell(self):
        import tomllib
        settings = tomllib.loads((ROOT / "configs/alacritty/alacritty.toml").read_text())
        self.assertNotIn("shell", settings.get("terminal", {}))

    def test_bad_ideavim_markers_rejected_before_ide_is_changed(self):
        (self.home / ".ideavimrc").write_text('" BEGIN voidplacer JetBrains profile\n')
        configurator = Configurator(self.system(), Repository(ROOT), Mock(), Backups(self.home))
        with self.assertRaisesRegex(SetupError, "Malformed"):
            configurator.assert_ide_closed("clion")

    def test_existing_lazyvim_kept_and_customizations_backed_up(self):
        system = self.system()
        destination = self.home / ".config/nvim"
        (destination / "lua/config").mkdir(parents=True)
        (destination / "lua/config/lazy.lua").write_text('return { "LazyVim/LazyVim" }')
        (destination / "lua/config/options.lua").write_text("vim.opt.number = false")
        (destination / "lua/config/keymaps.lua").write_text("old keys")
        (destination / "lazyvim.json").write_text('{"extras":["personal.extra"],"version":8}')
        repository = Repository(ROOT)
        backups = Backups(self.home)
        configurator = Configurator(system, repository, Mock(), backups)
        stage = self.home / "stage"
        repository.stage("configs/nvim", stage)
        configurator.nvim(stage)
        self.assertEqual((destination / "lua/config/options.lua").read_text(), "vim.opt.number = false")
        self.assertIn("personal.extra", json.loads((destination / "lazyvim.json").read_text())["extras"])
        self.assertNotIn("lazyvim.plugins.extras.lang.java", json.loads((destination / "lazyvim.json").read_text())["extras"])
        self.assertFalse((self.home / ".clang-format").exists())
        (self.home / ".clang-format").write_text("Existing formatting preference\n")
        configurator.nvim(stage)
        self.assertEqual((self.home / ".clang-format").read_text(), "Existing formatting preference\n")
        record = next(item for item in backups.records if item["path"].endswith("/keymaps.lua"))
        self.assertEqual((backups.root / record["backup"]).read_text(), "old keys")

    def test_only_selected_configuration_is_written(self):
        system = self.system()
        repository = Repository(ROOT)
        backups = Backups(self.home)
        configurator = Configurator(system, repository, Mock(), backups)
        entry = next(item for item in repository.read_json("catalog.json")["applications"] if item["id"] == "kitty")
        stage = self.home / "stage"
        repository.stage(entry["prefix"], stage)
        configurator.apply(entry, stage)
        self.assertTrue((self.home / ".config/kitty/kitty.conf").is_file())
        self.assertFalse((self.home / ".tmux.conf").exists())

    def test_xml_merge_preserves_unrelated_options(self):
        target = ET.fromstring('<component name="Editor"><option name="keep" value="yes"/><option name="change" value="old"/></component>')
        source = ET.fromstring('<component name="Editor"><option name="change" value="new"/></component>')
        Configurator.merge_xml(target, source)
        self.assertEqual(target.find('option[@name="keep"]').get("value"), "yes")
        self.assertEqual(target.find('option[@name="change"]').get("value"), "new")

    def test_xml_merge_matches_individual_vim_conflicts(self):
        target = ET.fromstring('<shortcut-conflicts><shortcut-conflict owner="ide"><text>first</text></shortcut-conflict></shortcut-conflicts>')
        source = ET.fromstring('<shortcut-conflicts><shortcut-conflict owner="vim"><text>first</text></shortcut-conflict><shortcut-conflict owner="ide"><text>second</text></shortcut-conflict></shortcut-conflicts>')
        Configurator.merge_xml(target, source)
        self.assertEqual(len(target), 2)
        self.assertEqual(target[0].get("owner"), "vim")

    def test_builtin_plugin_artifact_matches_sources(self):
        base = ROOT / "plugins/jetbrains/coding-profile"
        release = json.loads((base / "release.json").read_text())
        self.assertEqual(hashlib.sha256(base64.b64decode(release["artifact"])).hexdigest(), release["sha256"])
        for name, checksum in release["sources"].items():
            self.assertEqual(hashlib.sha256((base / name).read_bytes()).hexdigest(), checksum)
        import zipfile
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(release["artifact"]))) as bundle:
            for name in bundle.namelist():
                content = bundle.read(name)
                self.assertNotIn(b"/home/", content)
                self.assertNotIn(b"/Users/", content)

    def test_profile_conflicts_rejected(self):
        for identities in (("ideavim", "clion"), ("caps-lock-on", "caps-lock-off")):
            with self.assertRaises(SetupError):
                installer.validate_selection([{"id": identity} for identity in identities])


class KeyboardTests(TemporaryHome):
    @patch("editor_setup.keyboard.run", return_value=result("/missing-homebrew"))
    def test_macos_login_agent_reuses_running_python_when_brew_python_is_absent(self, execute):
        repository = Repository(ROOT)
        keyboard = Keyboard(self.system("macos"), Backups(self.home), repository)
        keyboard.macos_agent(True)
        agent = self.home / "Library/LaunchAgents/local.myeditorconfig.caps-escape.plist"
        settings = plistlib.loads(agent.read_bytes())
        self.assertEqual(settings["ProgramArguments"][0], sys.executable)

    def test_explicit_keyboard_target_is_idempotent(self):
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "GNOME"}):
            keyboard = Keyboard(self.system(), Backups(self.home), Mock(), assume_yes=True)
        keyboard.read = Mock(return_value={"options": ["caps:escape"]})
        with patch("editor_setup.keyboard.run") as execute:
            keyboard.toggle(True)
            execute.assert_not_called()
        keyboard.read.return_value = {"options": []}
        with patch("editor_setup.keyboard.run") as execute:
            keyboard.toggle(False)
            execute.assert_not_called()
        self.assertFalse(keyboard.state_path.exists())

    def test_macos_mapping_parser(self):
        output = f"(\n{{ HIDKeyboardModifierMappingSrc = {CAPS}; HIDKeyboardModifierMappingDst = {ESCAPE}; }}\n)"
        self.assertEqual(mac_mappings(output)[0]["HIDKeyboardModifierMappingDst"], ESCAPE)
        self.assertEqual(mac_mappings("(null)"), [])
        self.assertEqual(mac_mappings("(\n    )"), [])

    def test_unknown_hidutil_output_refused(self):
        with self.assertRaises(SetupError):
            mac_mappings("something unexpected")

    @patch("editor_setup.keyboard.run")
    def test_gnome_toggle_restores_caps_preserving_unrelated_changes(self, execute):
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "GNOME"}):
            keyboard = Keyboard(self.system(), Backups(self.home), Mock(), assume_yes=True)
        state = {"options": ["compose:ralt", "caps:ctrl_modifier"]}
        keyboard.read = lambda: {"options": list(state["options"])}
        def command(arguments, **kwargs):
            if arguments[:2] == ["gsettings", "set"]:
                import ast
                state["options"] = ast.literal_eval(arguments[-1])
            return result()
        execute.side_effect = command
        keyboard.toggle()
        self.assertEqual(state["options"], ["compose:ralt", "caps:escape"])
        state["options"].append("grp:alt_shift_toggle")
        keyboard.toggle()
        self.assertEqual(state["options"], ["compose:ralt", "grp:alt_shift_toggle", "caps:ctrl_modifier"])

    def test_unknown_desktop_is_unsupported(self):
        keyboard = Keyboard(self.system(), Backups(self.home), Mock())
        status, reason = keyboard.status()
        self.assertEqual(status, "unsupported/unknown")
        self.assertIn("GNOME", reason)

    @patch("editor_setup.keyboard.run")
    @patch("editor_setup.keyboard.shutil.which")
    def test_kde_toggle_preserves_options_and_restores_reset_flag(self, locate, execute):
        locate.side_effect = lambda name: "/usr/bin/" + name
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "KDE"}):
            keyboard = Keyboard(self.system(), Backups(self.home), Mock(), assume_yes=True)
        state = {"options": ["compose:ralt"], "reset": False}
        keyboard.read = lambda: {"options": list(state["options"]), "reset": state["reset"]}
        def command(arguments, **kwargs):
            if "--key" in arguments:
                key = arguments[arguments.index("--key") + 1]
                if key == "Options":
                    state["options"] = arguments[-1].split(",")
                elif key == "ResetOldOptions":
                    state["reset"] = arguments[-1] == "true"
            return result()
        execute.side_effect = command
        keyboard.toggle()
        self.assertIn("caps:escape", state["options"])
        self.assertTrue(state["reset"])
        keyboard.toggle()
        self.assertEqual(state["options"], ["compose:ralt"])
        self.assertFalse(state["reset"])

    @patch("editor_setup.keyboard.run")
    def test_macos_toggle_preserves_other_mappings(self, execute):
        keyboard = Keyboard(self.system("macos"), Backups(self.home), Mock(), assume_yes=True)
        keyboard.macos_agent = Mock()
        unrelated = {"HIDKeyboardModifierMappingSrc": 1, "HIDKeyboardModifierMappingDst": 2}
        original_caps = {"HIDKeyboardModifierMappingSrc": CAPS, "HIDKeyboardModifierMappingDst": 3}
        state = {"mappings": [unrelated, original_caps]}
        keyboard.read = lambda: {"mappings": list(state["mappings"])}
        def command(arguments, **kwargs):
            if "--set" in arguments:
                state["mappings"] = json.loads(arguments[-1])["UserKeyMapping"]
            return result()
        execute.side_effect = command
        keyboard.toggle()
        self.assertIn(unrelated, state["mappings"])
        self.assertTrue(keyboard.enabled(state))
        keyboard.toggle()
        self.assertEqual(state["mappings"], [unrelated, original_caps])
        self.assertEqual(keyboard.macos_agent.call_args_list[0].args, (True,))
        self.assertEqual(keyboard.macos_agent.call_args_list[1].args, (False,))


class CommandLineTests(TemporaryHome):
    def test_bootstrap_help_does_not_run_package_setup(self):
        commands = self.home / "bin"
        commands.mkdir()
        marker = self.home / "unexpected-package-setup"
        for name, body in (("uname", "printf 'Darwin\\n'"), ("sw_vers", f"touch '{marker}'; exit 1"), ("brew", f"touch '{marker}'; exit 1")):
            executable = commands / name
            executable.write_text("#!/bin/sh\n" + body + "\n")
            executable.chmod(0o755)
        completed = subprocess.run(["/bin/bash", str(ROOT / "install.sh"), "--local", "--help"], capture_output=True, text=True, timeout=15, env=dict(os.environ, PATH=str(commands) + os.pathsep + os.environ.get("PATH", os.defpath), PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("usage:", completed.stdout)
        self.assertFalse(marker.exists())

    @patch("installer.Configurator")
    @patch("installer.Packages")
    def test_neovim_only_does_not_enable_extras_or_install_jdk(self, packages, configurator):
        with patch("installer.Platform", return_value=self.system()), patch("installer.os.geteuid", return_value=1000), patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(installer.main(["--select", "nvim", "--apply", "--yes"]), 0)
        packages.return_value.lazyvim.assert_called_once_with([])
        packages.return_value.ensure_jdk.assert_not_called()
        action = configurator.return_value.apply.call_args.args[0]
        self.assertEqual(action["extras"], [])
        self.assertFalse(action["requires_jdk"])

    @patch("installer.Configurator")
    @patch("installer.Packages")
    def test_selected_extras_apply_once_with_their_parent(self, packages, configurator):
        packages.return_value.ensure_jdk.return_value = self.home / "jdk25"
        with patch("installer.Platform", return_value=self.system()), patch("installer.os.geteuid", return_value=1000), patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(installer.main(["--select", "nvim-clangd,nvim-java", "--apply", "--yes"]), 0)
        packages.return_value.lazyvim.assert_called_once_with(["clangd"])
        packages.return_value.ensure_jdk.assert_called_once()
        configurator.return_value.apply.assert_called_once()
        action = configurator.return_value.apply.call_args.args[0]
        self.assertEqual(action["extras"], ["lang.clangd", "lang.java"])
        self.assertEqual(action["java_home"], str(self.home / "jdk25"))

    @patch("installer.Configurator")
    @patch("installer.Packages")
    def test_failed_jdk_check_does_not_apply_java_or_neovim_config(self, packages, configurator):
        packages.return_value.ensure_jdk.side_effect = SetupError("Unsupported JDK")
        with patch("installer.Platform", return_value=self.system()), patch("installer.os.geteuid", return_value=1000), patch("sys.stdout", new_callable=io.StringIO), patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(installer.main(["--select", "nvim-java", "--apply", "--yes"]), 1)
        configurator.return_value.apply.assert_not_called()

    @patch("installer.Packages")
    def test_dry_run_does_not_call_package_manager(self, packages):
        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(installer.main(["--select", "kitty", "--dry-run"]), 0)
        packages.return_value.install_application.assert_not_called()
        packages.return_value.refresh.assert_not_called()
        self.assertFalse((self.home / ".config").exists())
        self.assertFalse((self.home / "editor_backup").exists())

    @patch("installer.Configurator")
    @patch("installer.Packages")
    def test_package_failure_never_applies_configuration(self, packages, configurator):
        packages.return_value.install_application.side_effect = SetupError("unsupported package")
        with patch("sys.stdout", new_callable=io.StringIO), patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(installer.main(["--select", "kitty", "--apply", "--yes"]), 1)
        configurator.return_value.apply.assert_not_called()

    def test_explicit_selection_requires_apply_or_dry_run(self):
        with patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit):
                installer.main(["--select", "kitty"])


class MenuTests(unittest.TestCase):
    def test_neovim_extras_are_nested_optional_and_depend_on_parent(self):
        entries = [entry for entry in json.loads((ROOT / "catalog.json").read_text())["applications"] if entry["id"] in ("nvim", "nvim-clangd", "nvim-java")]
        model = Selection(entries)
        model.activate(0)
        self.assertEqual(len(model.rows()), 4)
        model.activate(1)
        self.assertEqual(model.selected, {"nvim"})
        self.assertEqual([row[1]["id"] for row in model.rows() if row[0] == "entry"], ["nvim", "nvim-clangd", "nvim-java"])
        self.assertTrue(any("|-- [ ] C/C++ support" in label for index, label in spaced_rows(model)))
        model.activate(2)
        model.activate(3)
        self.assertEqual(model.selected, {"nvim", "nvim-clangd", "nvim-java"})
        model.back()
        self.assertEqual(model.cursor, 1)
        self.assertEqual(model.selected, {"nvim", "nvim-clangd", "nvim-java"})
        model.open()
        model.activate(1)
        self.assertEqual(model.selected, set())
        self.assertEqual(len(model.rows()), 4)

    def test_opening_extra_branch_does_not_select_neovim_but_checking_extra_does(self):
        entries = [entry for entry in json.loads((ROOT / "catalog.json").read_text())["applications"] if entry["id"] in ("nvim", "nvim-clangd", "nvim-java")]
        model = Selection(entries)
        model.open()
        model.cursor = 1
        model.open()
        self.assertEqual(model.selected, set())
        model.activate(3)
        self.assertEqual(model.selected, {"nvim", "nvim-java"})

    def test_formatting_styles_auto_uncheck_each_other_not_neovim(self):
        entries = [entry for entry in json.loads((ROOT / "catalog.json").read_text())["applications"] if entry["id"] in ("nvim", "formatting", "formatting-allman")]
        model = Selection(entries)
        model.expanded.update(model.groups)
        for identity, expected in (("nvim", {"nvim"}), ("formatting", {"nvim", "formatting"}), ("formatting-allman", {"nvim", "formatting-allman"}), ("formatting", {"nvim", "formatting"})):
            index = next(index for index, (action, value, label) in enumerate(model.rows()) if action == "entry" and value["id"] == identity)
            model.activate(index)
            self.assertEqual(model.selected, expected)
            installer.validate_selection([entry for entry in entries if entry["id"] in model.selected])

    def assert_complete_tree_numbering(self, entries):
        model = Selection(entries)
        model.expanded.update(model.groups)
        total = len(model.rows())
        number_width = max(2, len(str(total)))
        numbers = set()
        window = Mock()
        window.getmaxyx.return_value = (24, 120)
        window.get_wch.side_effect = [*(["j"] * (total - 1)), "q"]
        def record(row, column, text, limit, style):
            if 8 <= row < 20:
                field = text[2:2 + number_width]
                if field.strip().isdigit():
                    number = int(field)
                    self.assertEqual(field, f"{number:>{number_width}}")
                    self.assertEqual(text[2 + number_width:4 + number_width], "  ")
                    numbers.add(number)
        window.addnstr.side_effect = record
        with patch.dict(os.environ, {"TERM": "xterm"}), patch("editor_setup.menu.Selection", return_value=model), patch("editor_setup.menu.curses.curs_set"), patch("editor_setup.menu.palette", return_value={}), patch("editor_setup.menu.curses.wrapper", side_effect=lambda callback: callback(window)):
            self.assertEqual(choose(entries), [])
        self.assertEqual(numbers, set(range(1, total + 1)))

    def test_all_expanded_categories_show_numbers_beyond_nine(self):
        entries = json.loads((ROOT / "catalog.json").read_text())["applications"]
        entries += [{"id": "font-example", "name": "Font", "handler": "font"}, {"id": "caps-lock-on", "name": "Enable", "handler": "keyboard", "status": "Current: Disabled"}]
        self.assert_complete_tree_numbering(entries)

    def test_three_digit_row_numbers_remain_aligned(self):
        self.assert_complete_tree_numbering([{"id": f"tool-{index}", "name": f"Tool {index}"} for index in range(120)])

    def test_numbers_recalculate_when_branches_collapse(self):
        entries = [{"id": identity, "name": identity} for identity in ("kitty", "tmux", "alacritty", "bash", "zsh")]
        entries += [{"id": f"tool-{index}", "name": f"Tool {index}"} for index in range(10)]
        model = Selection(entries)
        model.expanded.update(model.groups)
        frame = []
        window = Mock()
        window.getmaxyx.return_value = (50, 120)
        window.get_wch.side_effect = ["h", "j", " ", "q"]
        window.erase.side_effect = frame.clear
        def record(row, column, text, limit, style):
            if 8 <= row < 46 and text[2:4].strip().isdigit():
                frame.append(int(text[2:4]))
        window.addnstr.side_effect = record
        def check_frame():
            self.assertEqual(frame, list(range(1, len(model.rows()) + 1)))
        window.refresh.side_effect = check_frame
        with patch.dict(os.environ, {"TERM": "xterm"}), patch("editor_setup.menu.Selection", return_value=model), patch("editor_setup.menu.curses.curs_set"), patch("editor_setup.menu.palette", return_value={}), patch("editor_setup.menu.curses.wrapper", side_effect=lambda callback: callback(window)):
            self.assertEqual(choose(entries), [])
        self.assertEqual(model.expanded, {"Developer tools"})

    def test_contextual_help_matches_expansion_state(self):
        model = Selection([{"id": "kitty", "name": "kitty"}])
        self.assertIn("expands this folder", model.help())
        model.activate(0)
        self.assertIn("collapses this folder", model.help())
        model.cursor = len(model.rows()) - 2
        self.assertIn("No configs selected", model.help())

    def test_keyboard_status_and_explanation_remain_explicit_inline(self):
        entries = [{"id": "caps-lock-on", "name": "Enable Caps Lock as Escape", "handler": "keyboard", "status": "Current: Disabled", "desired": True}, {"id": "caps-lock-off", "name": "Disable Caps Lock as Escape", "handler": "keyboard", "status": "Current: Disabled", "desired": False}]
        model = Selection(entries)
        model.activate(0)
        self.assertIn("Choose Enable or Disable below", model.help())
        self.assertTrue(any("Current: Disabled" in label for index, label in spaced_rows(model)))
        model.cursor = 1
        self.assertIn("Enable: Caps Lock sends Escape", model.help())
        model.cursor = 2
        self.assertIn("Disable: restore", model.help())

    @patch.dict(os.environ, {"TERM": "xterm"})
    def test_resize_to_plain_mode_preserves_checked_items(self):
        import curses
        entry = {"id": "kitty", "name": "kitty"}
        window = Mock()
        window.getmaxyx.side_effect = [(24, 80), (24, 80), (24, 80), (10, 40)]
        window.get_wch.side_effect = ["1", "2", curses.KEY_RESIZE]
        with patch("editor_setup.menu.curses.curs_set"), patch("editor_setup.menu.palette", return_value={}), patch("editor_setup.menu.curses.wrapper", side_effect=lambda callback: callback(window)), patch("builtins.input", return_value="3"), patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(choose([entry]), [entry])

    @patch.dict(os.environ, {"TERM": "xterm"})
    def test_long_labels_use_visible_ellipsis(self):
        window = Mock()
        window.getmaxyx.return_value = (24, 60)
        window.get_wch.side_effect = ["1", "q"]
        with patch("editor_setup.menu.curses.curs_set"), patch("editor_setup.menu.palette", return_value={}), patch("editor_setup.menu.curses.wrapper", side_effect=lambda callback: callback(window)):
            self.assertEqual(choose([{"id": "kitty", "name": "Terminal settings " + "long name " * 12}]), [])
        self.assertTrue(any(call.args[2].endswith("...") for call in window.addnstr.call_args_list))

    def test_right_opens_and_left_collapses_without_losing_checks(self):
        model = Selection([{"id": "kitty", "name": "kitty"}, {"id": "tmux", "name": "tmux"}])
        model.open()
        self.assertEqual(model.expanded, {"Terminals"})
        self.assertEqual(model.cursor, 0)
        model.open()
        self.assertEqual(model.cursor, 1)
        model.activate(1)
        model.open()
        self.assertEqual(model.selected, {"kitty"})
        model.back()
        self.assertEqual(model.cursor, 0)
        self.assertEqual(model.expanded, set())
        self.assertEqual(model.selected, {"kitty"})
        model.open()
        self.assertIn("[x] kitty", model.rows()[1][2])

    def test_viewport_is_stable_and_keeps_focus_visible(self):
        model = Selection([{"id": str(index), "name": f"Entry {index}"} for index in range(30)])
        model.open()
        lines = spaced_rows(model)
        self.assertEqual(viewport(lines, 2, 8, 0), 0)
        for size in (6, 12, 24):
            start = 0
            for cursor in [*range(len(model.rows())), *reversed(range(len(model.rows())))]:
                start = viewport(lines, cursor, size, start)
                self.assertIn(cursor, [index for index, label in lines[start:start + size]])

    def test_all_collapsed_categories_and_actions_fit_standard_terminal(self):
        entries = json.loads((ROOT / "catalog.json").read_text())["applications"]
        entries += [{"id": "font-example", "name": "Font", "handler": "font"}, {"id": "caps-lock-on", "name": "Enable", "handler": "keyboard"}]
        model = Selection(entries)
        self.assertEqual(len(model.groups), 6)
        self.assertLessEqual(len(spaced_rows(model)), 24 - 8 - 4)

    def test_readability_and_bounds_across_terminal_sizes(self):
        entries = json.loads((ROOT / "catalog.json").read_text())["applications"]
        entries += [{"id": "font-example", "name": "Font", "handler": "font"}, {"id": "caps-lock-on", "name": "Enable Caps Lock as Escape", "handler": "keyboard", "status": "Current: Disabled"}]
        for height, width in ((18, 60), (24, 80), (30, 100), (40, 120)):
            with self.subTest(height=height, width=width):
                frame = {}
                frames = []
                window = Mock()
                window.getmaxyx.return_value = (height, width)
                window.erase.side_effect = frame.clear
                window.refresh.side_effect = lambda: frames.append(dict(frame))
                window.get_wch.side_effect = ["1", "j", " ", "h", "j", " ", "j", " ", *(["j"] * 22), *(["k"] * 22), "q"]
                def draw(row, column, text, limit, style):
                    self.assertTrue(0 <= row < height)
                    self.assertTrue(0 <= column < width)
                    self.assertLessEqual(len(text), limit)
                    self.assertLess(column + len(text), width)
                    self.assertTrue(text.isascii())
                    self.assertNotIn(row, frame, "Header, tree, and footer must not overlap")
                    frame[row] = text
                window.addnstr.side_effect = draw
                with patch.dict(os.environ, {"TERM": "xterm"}), patch("editor_setup.menu.curses.curs_set"), patch("editor_setup.menu.palette", return_value={}), patch("editor_setup.menu.curses.wrapper", side_effect=lambda callback: callback(window)):
                    self.assertEqual(choose(entries), [])
                self.assertTrue(all("q quit" in frame[height - 2] for frame in frames))
                if height >= 24:
                    root = "\n".join(frames[0].values())
                    self.assertIn("Keyboard/", root)
                    self.assertIn("Install selected", root)
                    self.assertIn("Quit without installing", root)
                expanded = "\n".join(frames[1].values())
                self.assertIn("[/] [-] Editors and IDEs/", expanded)
                self.assertIn("|-- [ ]", expanded)

    def test_spacing_does_not_add_navigation_targets(self):
        model = Selection([{"id": "kitty", "name": "kitty"}, {"id": "bash", "name": "Bash"}])
        lines = spaced_rows(model)
        self.assertEqual([index for index, label in lines if index is not None], list(range(len(model.rows()))))
        install_line = next(position for position, (index, label) in enumerate(lines) if index == len(model.rows()) - 2)
        self.assertEqual([label for index, label in lines[install_line - 3:install_line]], ["", "", "Actions"])
        self.assertEqual(lines[:2], [(0, model.rows()[0][2]), (1, model.rows()[1][2])])

    def test_inline_expansion_keeps_parent_and_siblings_visible(self):
        model = Selection([{"id": "kitty", "name": "kitty"}, {"id": "bash", "name": "Bash"}])
        model.activate(1)
        self.assertEqual(model.breadcrumb(), "Configuration tree / Shells")
        self.assertEqual([row[0] for row in model.rows()], ["folder", "folder", "entry", "install", "quit"])
        labels = [label for index, label in spaced_rows(model)]
        self.assertTrue(any("`-- [ ] Bash" in label for label in labels))
        model.activate(2)
        model.back()
        self.assertEqual(model.expanded, set())
        self.assertEqual(model.cursor, 1)
        self.assertEqual(model.selected, {"bash"})

    @patch("editor_setup.menu.curses")
    def test_supported_terminal_uses_three_colour_accents(self, terminal):
        terminal.COLORS = 8
        terminal.COLOR_PAIRS = 16
        terminal.has_colors.return_value = True
        terminal.color_pair.side_effect = lambda index: index << 8
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(palette(), {"accent": 256, "selected": 512, "warning": 768})
        self.assertEqual(terminal.init_pair.call_count, 3)
        terminal.use_default_colors.assert_called_once()

    @patch.dict(os.environ, {"NO_COLOR": "1"})
    @patch("editor_setup.menu.curses.start_color")
    def test_no_color_disables_palette(self, start):
        self.assertEqual(palette(), {})
        start.assert_not_called()

    @patch("editor_setup.menu.curses.start_color")
    def test_colour_failure_falls_back_to_monochrome(self, start):
        import curses
        start.side_effect = curses.error("No colour support")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(palette(), {})

    def test_icons_are_ascii_and_actions_are_distinct(self):
        model = Selection([{"id": "kitty", "name": "kitty"}])
        labels = [row[2] for row in model.rows()]
        self.assertTrue(all(label.isascii() for label in labels))
        self.assertTrue(labels[0].startswith("[/]"))
        self.assertTrue(labels[-2].startswith("[>]"))
        self.assertTrue(labels[-1].startswith("[q]"))

    def test_categories_and_selections_persist(self):
        entries = [{"id": "kitty", "name": "kitty"}, {"id": "bash", "name": "Bash"}]
        model = Selection(entries)
        model.activate(0)
        self.assertEqual(model.expanded, {"Terminals"})
        model.activate(1)
        model.activate(2)
        self.assertEqual(model.expanded, {"Terminals", "Shells"})
        model.activate(3)
        self.assertEqual(model.activate(4), entries)

    def test_keyboard_choices_are_mutually_exclusive(self):
        entries = [{"id": "caps-lock-on", "name": "Enable", "handler": "keyboard"}, {"id": "caps-lock-off", "name": "Disable", "handler": "keyboard"}]
        model = Selection(entries)
        model.activate(0)
        model.activate(1)
        model.activate(2)
        self.assertEqual(model.selected, {"caps-lock-off"})

    def test_unsupported_entry_cannot_be_selected(self):
        model = Selection([{"id": "kitty", "name": "kitty", "unsupported": "Not available"}])
        model.activate(0)
        model.activate(1)
        self.assertEqual(model.selected, set())
        model.cursor = 1
        self.assertEqual(model.help(), "Not available")

    @patch.dict(os.environ, {"TERM": "dumb"})
    @patch("builtins.input", side_effect=["1", "2", "3"])
    def test_plain_terminal_fallback(self, prompt):
        entry = {"id": "kitty", "name": "kitty"}
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(choose([entry]), [entry])
        self.assertIn("Designed by voidplacer", output.getvalue())
        self.assertIn("github.com/voidplacer/myeditorconfig", output.getvalue())

    @patch("editor_setup.menu.subprocess.run", side_effect=subprocess.TimeoutExpired("probe", 1))
    def test_information_probe_timeout_is_harmless(self, execute):
        self.assertEqual(probe(["ps"]), "")

    @patch("editor_setup.menu.probe", return_value="")
    def test_unknown_system_details_do_not_fail(self, execute):
        system = Mock(kind="ubuntu", release="24.04", machine="x86_64")
        with patch.dict(os.environ, {}, clear=True):
            details = system_details(system)
        self.assertIn("Ubuntu 24.04", details[0])
        self.assertTrue(all("unknown" in detail for detail in details[1:]))

    @patch("editor_setup.menu.probe")
    def test_desktop_terminal_and_shell_information(self, execute):
        def response(arguments):
            if arguments[0] == "ps":
                return "1 /usr/bin/zsh"
            return {"plasmashell": "plasmashell 6.5.0", "zsh": "zsh 5.9"}.get(arguments[0], "")
        execute.side_effect = response
        system = Mock(kind="fedora", release="43", machine="x86_64")
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "KDE", "KONSOLE_VERSION": "250801"}, clear=True):
            details = system_details(system)
        self.assertIn("plasmashell 6.5.0", details[1])
        self.assertIn("konsole | 25.8.1", details[2])
        self.assertIn("zsh 5.9", details[3])

    @patch.dict(os.environ, {"TERM": "xterm"})
    @patch("editor_setup.menu.curses.wrapper")
    @patch("builtins.input", return_value="q")
    def test_small_terminal_falls_back_without_failure(self, prompt, wrapper):
        window = Mock()
        window.getmaxyx.return_value = (8, 35)
        wrapper.side_effect = lambda callback: callback(window)
        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(choose([{"id": "kitty", "name": "kitty"}]), [])

    @patch.dict(os.environ, {"TERM": "xterm"})
    @patch("editor_setup.menu.curses.curs_set")
    @patch("editor_setup.menu.curses.wrapper")
    def test_space_on_install_proceeds(self, wrapper, cursor):
        window = Mock()
        window.getmaxyx.return_value = (24, 100)
        window.get_wch.side_effect = ["1", "2", "j", "j", "k", " "]
        wrapper.side_effect = lambda callback: callback(window)
        entry = {"id": "kitty", "name": "kitty"}
        self.assertEqual(choose([entry]), [entry])

    @patch("editor_setup.menu.curses.curs_set")
    @patch("editor_setup.menu.curses.wrapper")
    @patch.dict(os.environ, {"TERM": "xterm"})
    def test_scroll_reaches_entries_beyond_nine(self, wrapper, cursor):
        window = Mock()
        window.getmaxyx.return_value = (18, 100)
        window.get_wch.side_effect = ["1", *(["j"] * 11), " ", "j", " "]
        wrapper.side_effect = lambda callback: callback(window)
        entries = [{"id": str(index), "name": f"entry {index}"} for index in range(11)]
        self.assertEqual(choose(entries), [entries[10]])


if __name__ == "__main__":
    unittest.main()
