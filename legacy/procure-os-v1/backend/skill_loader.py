"""
Loads every skill module under skills/<domain>/*.py, and builds
Groq-compatible tool schemas from each skill's declared metadata
(SKILL_NAME, SKILL_DESCRIPTION, SKILL_PARAMETERS, run()).
"""
import importlib
import pkgutil
import skills


def _iter_skill_modules(package):
    modules = []
    for _finder, name, ispkg in pkgutil.iter_modules(package.__path__, package.__name__ + "."):
        if ispkg:
            continue
        mod = importlib.import_module(name)
        if hasattr(mod, "SKILL_NAME"):
            modules.append(mod)
    return modules


def load_all_skills():
    """Returns {skill_name: module} for every skill in every domain folder."""
    all_modules = {}
    for _finder, subpkg_name, ispkg in pkgutil.iter_modules(skills.__path__, skills.__name__ + "."):
        if not ispkg:
            continue
        subpkg = importlib.import_module(subpkg_name)
        for mod in _iter_skill_modules(subpkg):
            all_modules[mod.SKILL_NAME] = mod
    return all_modules


def build_tools(skill_names, all_modules):
    """Given a list of skill names an agent owns, build (tools, skill_map)."""
    tools = []
    skill_map = {}
    for name in skill_names:
        mod = all_modules[name]
        tools.append({
            "type": "function",
            "function": {
                "name": mod.SKILL_NAME,
                "description": mod.SKILL_DESCRIPTION,
                "parameters": mod.SKILL_PARAMETERS,
            },
        })
        skill_map[name] = mod
    return tools, skill_map
