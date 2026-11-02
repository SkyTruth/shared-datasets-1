from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / '.claude/skills/publish-shared-dataset/SKILL.md'


def linked_markdown(path):
    for target in re.findall(r'\]\(([^)#]+)(?:#[^)]*)?\)', path.read_text()):
        if '://' not in target:
            yield (path.parent / target).resolve()


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
    assert 'Data / Approved mutation' in proposal
    assert 'shared-datasets-production' in proposal
    assert '.github/dataset-plans/' in proposal
