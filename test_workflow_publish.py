"""Exercise the actual workflow shell against competing local Git clones."""
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parent


def run(cwd, *args, check=True, env=None):
    return subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, check=check, env=env)


def publish_script(workflow):
    text = (ROOT / '.github/workflows' / workflow).read_text()
    block = text.split('      - name: 저장소에 반영\n', 1)[1]
    block = block.split('        run: |\n', 1)[1]
    return textwrap.dedent(block.split('\n      - ', 1)[0])


class PublishTests(unittest.TestCase):
    def fixture(self, root):
        remote, local, peer = (root / n for n in ('remote.git', 'local', 'peer'))
        run(root, 'git', 'init', '--bare', '--initial-branch=main', str(remote))
        run(root, 'git', 'clone', str(remote), str(local))
        self.identity(local)
        (local / 'data').mkdir()
        (local / 'data/base.json').write_text('{"base": 1}\n')
        (local / 'build.py').write_text(
            'import json\nfrom pathlib import Path\n'
            'd = {p.name: json.loads(p.read_text()) for p in Path("data").glob("*.json")}\n'
            's = json.dumps(d, sort_keys=True)\n'
            'Path("index.html").write_text(s)\n'
            'Path("version.txt").write_text(__import__("hashlib").sha256(s.encode()).hexdigest())\n')
        self.build(local)
        self.commit(local)
        run(local, 'git', 'push', 'origin', 'main')
        run(root, 'git', 'clone', str(remote), str(peer))
        self.identity(peer)
        return remote, local, peer

    def identity(self, path):
        run(path, 'git', 'config', 'user.name', 'Test')
        run(path, 'git', 'config', 'user.email', 'test@example.invalid')

    def commit(self, path):
        run(path, 'git', 'add', '.')
        run(path, 'git', 'commit', '-m', 'fixture')

    def build(self, path):
        run(path, 'python', 'build.py')

    def publish(self, local, root, workflow):
        env = dict(os.environ, BR='main', RUNNER_TEMP=str(root),
                   GITHUB_RUN_ID='test', GITHUB_RUN_ATTEMPT='1')
        return run(local, 'bash', '-e', '-c', publish_script(workflow), check=False, env=env)

    def scenario(self, workflow, mode):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote, local, peer = self.fixture(root)
            before = run(peer, 'git', 'rev-parse', 'HEAD').stdout.strip()
            if mode == 'unchanged':
                result = self.publish(local, root, workflow)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertEqual(run(remote, 'git', 'rev-parse', 'main').stdout.strip(), before)
                return
            (local / 'data/local.json').write_text('{"collected": 2}\n')
            (peer / 'data/peer.json').write_text('{"collected": 3}\n')
            if mode == 'data-conflict':
                (local / 'data/base.json').write_text('{"base": 2}\n')
                (peer / 'data/base.json').write_text('{"base": 3}\n')
            elif mode == 'code-conflict':
                (local / 'build.py').write_text('raise RuntimeError("local")\n')
                run(local, "git", "add", "build.py")
                run(local, "git", "commit", "-m", "local code edit")
                (peer / 'build.py').write_text('raise RuntimeError("remote")\n')
            else:
                self.build(local)
                self.build(peer)
            self.commit(peer)
            peer_sha = run(peer, 'git', 'rev-parse', 'HEAD').stdout.strip()
            if mode == 'push-race':
                hook = local / '.git/hooks/pre-push'
                hook.write_text('#!/bin/sh\nrm -- "$0"\ngit -C "' + str(peer) + '" push origin main\n')
                hook.chmod(0o755)
            else:
                run(peer, 'git', 'push', 'origin', 'main')
            result = self.publish(local, root, workflow)
            if mode.endswith('conflict'):
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(run(remote, 'git', 'rev-parse', 'main').stdout.strip(), peer_sha)
                self.assertTrue((root / 'collected-data.tar.gz').exists(), result.stdout)
                with tarfile.open(root / 'collected-data.tar.gz') as backup:
                    self.assertEqual(json.load(backup.extractfile('data/local.json')), {'collected': 2})
                return
            self.assertEqual(result.returncode, 0, result.stdout)
            content = run(remote, 'git', 'show', 'main:index.html').stdout
            self.assertEqual(json.loads(content), {
                'base.json': {'base': 1}, 'local.json': {'collected': 2},
                'peer.json': {'collected': 3}})
            run(local, 'git', 'fetch', 'origin')
            self.assertEqual(run(local, 'git', 'merge-base', '--is-ancestor', peer_sha, 'origin/main').returncode, 0)
            self.assertEqual(run(local, 'git', 'status', '--porcelain').stdout, '')
            if mode == 'push-race':
                self.assertIn('rejected', result.stdout)

    def test_generated_conflicts_preserve_both_collectors(self):
        for workflow in ('collect.yml', 'numbers.yml'):
            with self.subTest(workflow=workflow):
                self.scenario(workflow, 'generated')

    def test_push_race_retries_without_losing_remote_data(self):
        for workflow in ('collect.yml', 'numbers.yml'):
            with self.subTest(workflow=workflow):
                self.scenario(workflow, 'push-race')

    def test_data_conflict_stops_and_retains_recovery_copy(self):
        for workflow in ('collect.yml', 'numbers.yml'):
            with self.subTest(workflow=workflow):
                self.scenario(workflow, 'data-conflict')

    def test_code_conflict_stops_and_retains_recovery_copy(self):
        for workflow in ('collect.yml', 'numbers.yml'):
            with self.subTest(workflow=workflow):
                self.scenario(workflow, 'code-conflict')

    def test_unchanged_does_not_commit(self):
        for workflow in ('collect.yml', 'numbers.yml'):
            with self.subTest(workflow=workflow):
                self.scenario(workflow, 'unchanged')


if __name__ == '__main__':
    unittest.main()
