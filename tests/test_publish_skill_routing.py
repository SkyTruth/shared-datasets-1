from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / '.claude/skills/publish-shared-dataset/SKILL.md'


def linked_markdown(path):
    for target in re.findall(r'\]\(([^)#]+)(?:#[^)]*)?\)', path.read_text()):
        if '://' not in target:
            yield (path.parent / target).resolve()


def test_publish_routes_are_discoverable_without_loading_all_references():
    links = set(linked_markdown(SKILL))
    for target in (
        'references/first-upload.md',
        'references/existing-asset-update.md',
        '../feature-preview/SKILL.md',
        'references/partial-publication-recovery.md',
    ):
        assert (SKILL.parent / target).resolve() in links
    assert len(SKILL.read_text().splitlines()) <= 200
    assert len((ROOT / 'scripts/README.md').read_text().splitlines()) <= 150


def test_moved_procedures_keep_resolvable_references_and_publisher_boundary():
    files = [SKILL, ROOT / 'scripts/README.md']
    for owner in ('publish-shared-dataset', 'gcp-shared-datasets', 'feature-preview',
                  'static-catalog-web-preview', 'deploy-scheduled-ingestion'):
        directory = ROOT / '.claude/skills' / owner
        files.extend(directory.rglob('*.md'))
    for path in files:
        for target in linked_markdown(path):
            assert target.is_file(), (path, target)
    proposal = (SKILL.parent / 'references/reviewed-proposal.md').read_text()
    assert 'Approved dataset mutation' in proposal
    assert 'shared-datasets-production' in proposal
    assert '.github/dataset-plans/' in proposal
