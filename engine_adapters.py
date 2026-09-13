from pathlib import Path

SKILL_PATH = Path(__file__).parent / '.trae' / 'skills' / 'engine-adapters' / 'SKILL.md'

def skill_for_engine(engine='godot'):
    """Return the shared engine adapter skill for injection into the local agent."""
    try:
        text = SKILL_PATH.read_text(encoding='utf-8')
    except OSError:
        return ''
    sections = text.split('\n## ')
    wanted = [text.split('\n## Godot 4',1)[0]]
    key = {'godot':'Godot 4','unity':'Unity','unreal':'Unreal Engine'}.get(engine, 'Godot 4')
    for section in sections[1:]:
        if section.startswith(key): wanted.append('## ' + section)
        if section.startswith('Shared workflow'): wanted.append('## ' + section)
    return '\n'.join(wanted) + f"\n\n当前项目引擎：{engine}"
