"""
Report Template Registry and Loader for SmartVerify Report Generation System.
Provides deterministic, versioned access to approved report templates.
"""
from typing import Dict, Tuple, List, Optional
from app.report_templates.schema import ReportTemplate
from app.report_templates.v1_0.standard_reverification import TEMPLATE_V1_0

# Global registry of versioned report templates: (template_key, template_version) -> ReportTemplate
TEMPLATE_REGISTRY: Dict[Tuple[str, str], ReportTemplate] = {
    (TEMPLATE_V1_0.template_key, TEMPLATE_V1_0.template_version): TEMPLATE_V1_0,
}


def get_template(
    template_key: str = "standard_reverification",
    template_version: str = "v1.0",
) -> ReportTemplate:
    """
    Retrieve an approved, versioned report template.
    Raises KeyError if the requested template_key and template_version are not registered.
    """
    lookup = (template_key.strip().lower(), template_version.strip().lower())
    for (k, v), tmpl in TEMPLATE_REGISTRY.items():
        if (k.lower(), v.lower()) == lookup:
            return tmpl
    raise KeyError(f"Report template '{template_key}' version '{template_version}' not found in registry.")


def list_templates() -> List[Dict[str, str]]:
    """List all registered templates with their key, version, name, and description."""
    return [
        {
            "template_key": tmpl.template_key,
            "template_version": tmpl.template_version,
            "name": tmpl.name,
            "description": tmpl.description,
        }
        for tmpl in TEMPLATE_REGISTRY.values()
    ]


__all__ = [
    "ReportTemplate",
    "TEMPLATE_REGISTRY",
    "get_template",
    "list_templates",
]
