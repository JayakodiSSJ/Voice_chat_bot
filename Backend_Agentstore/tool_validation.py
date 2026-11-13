from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from tool_types import ToolSpec


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    code: str
    message: str


@dataclass(frozen=True)
class ToolArgumentValidation:
    issues: tuple[ValidationIssue, ...]

    @property
    def valid(self) -> bool:
        return not self.issues

    @property
    def missing_parameters(self) -> list[str]:
        return self._paths_for_code("required")

    @property
    def invalid_parameters(self) -> list[str]:
        return sorted(
            {
                issue.path
                for issue in self.issues
                if issue.code != "required" and issue.path and issue.path != "$"
            }
        )

    @property
    def messages(self) -> list[str]:
        return [issue.message for issue in self.issues]

    def _paths_for_code(self, code: str) -> list[str]:
        return sorted({issue.path for issue in self.issues if issue.code == code and issue.path})


class ToolArgumentValidator:
    """Fail-closed validation for the JSON Schema features used by tool inputs."""

    def validate(self, tool: ToolSpec, arguments: Any) -> ToolArgumentValidation:
        issues: list[ValidationIssue] = []
        schema = tool.input_schema
        if not isinstance(schema, dict):
            return ToolArgumentValidation(
                (ValidationIssue("$", "invalid_schema", f"Tool {tool.id} has an invalid input schema."),)
            )
        self._validate_value(schema, arguments, "$", schema, issues, set())
        return ToolArgumentValidation(tuple(issues))

    def _validate_value(
        self,
        schema: dict[str, Any],
        value: Any,
        path: str,
        root_schema: dict[str, Any],
        issues: list[ValidationIssue],
        resolving_refs: set[str],
    ) -> None:
        resolved = self._resolve_schema(schema, root_schema, path, issues, resolving_refs)
        if resolved is None:
            return
        schema = resolved

        for child_schema in schema.get("allOf") or []:
            if isinstance(child_schema, dict):
                self._validate_value(child_schema, value, path, root_schema, issues, set(resolving_refs))

        for keyword in ("anyOf", "oneOf"):
            branches = schema.get(keyword)
            if not isinstance(branches, list) or not branches:
                continue
            passing = 0
            branch_issue_sets: list[list[ValidationIssue]] = []
            for branch in branches:
                branch_issues: list[ValidationIssue] = []
                if isinstance(branch, dict):
                    self._validate_value(branch, value, path, root_schema, branch_issues, set(resolving_refs))
                else:
                    branch_issues.append(ValidationIssue(path, "invalid_schema", f"Invalid {keyword} schema at {path}."))
                if not branch_issues:
                    passing += 1
                branch_issue_sets.append(branch_issues)
            valid = passing >= 1 if keyword == "anyOf" else passing == 1
            if not valid:
                issues.append(ValidationIssue(path, keyword, f"Value at {path} does not satisfy {keyword}."))
                if passing == 0 and branch_issue_sets:
                    issues.extend(min(branch_issue_sets, key=len))

        if "const" in schema and value != schema["const"]:
            issues.append(ValidationIssue(path, "const", f"Value at {path} must equal the configured constant."))
        enum_values = schema.get("enum")
        if isinstance(enum_values, list) and value not in enum_values:
            issues.append(ValidationIssue(path, "enum", f"Value at {path} is not one of the allowed values."))

        expected_types = schema.get("type")
        if expected_types is None and ("properties" in schema or "required" in schema):
            expected_types = "object"
        if expected_types is not None:
            allowed_types = expected_types if isinstance(expected_types, list) else [expected_types]
            allowed_types = [str(item) for item in allowed_types]
            if not any(self._matches_type(value, expected) for expected in allowed_types):
                issues.append(
                    ValidationIssue(
                        path,
                        "type",
                        f"Value at {path} must be {self._format_types(allowed_types)}.",
                    )
                )
                return

        if isinstance(value, dict):
            self._validate_object(schema, value, path, root_schema, issues, resolving_refs)
        elif isinstance(value, list):
            self._validate_array(schema, value, path, root_schema, issues, resolving_refs)
        elif isinstance(value, str):
            self._validate_string(schema, value, path, issues)
        elif self._is_number(value):
            self._validate_number(schema, value, path, issues)

    def _validate_object(
        self,
        schema: dict[str, Any],
        value: dict[str, Any],
        path: str,
        root_schema: dict[str, Any],
        issues: list[ValidationIssue],
        resolving_refs: set[str],
    ) -> None:
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = schema.get("required") if isinstance(schema.get("required"), list) else []
        for key in required:
            key = str(key)
            child_path = self._child_path(path, key)
            if key not in value or value.get(key) in (None, ""):
                issues.append(ValidationIssue(child_path, "required", f"Required value {child_path} is missing."))

        for key, child_value in value.items():
            child_path = self._child_path(path, str(key))
            child_schema = properties.get(key)
            if isinstance(child_schema, dict):
                self._validate_value(
                    child_schema,
                    child_value,
                    child_path,
                    root_schema,
                    issues,
                    set(resolving_refs),
                )
                continue
            additional = schema.get("additionalProperties", True)
            if additional is False:
                issues.append(
                    ValidationIssue(child_path, "additional_property", f"Unexpected tool argument {child_path}.")
                )
            elif isinstance(additional, dict):
                self._validate_value(
                    additional,
                    child_value,
                    child_path,
                    root_schema,
                    issues,
                    set(resolving_refs),
                )

    def _validate_array(
        self,
        schema: dict[str, Any],
        value: list[Any],
        path: str,
        root_schema: dict[str, Any],
        issues: list[ValidationIssue],
        resolving_refs: set[str],
    ) -> None:
        self._check_length(schema, value, path, issues, "Items")
        if schema.get("uniqueItems") is True:
            for index, item in enumerate(value):
                if item in value[:index]:
                    issues.append(ValidationIssue(path, "uniqueItems", f"Items at {path} must be unique."))
                    break
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                self._validate_value(
                    item_schema,
                    item,
                    f"{path}[{index}]",
                    root_schema,
                    issues,
                    set(resolving_refs),
                )

    def _validate_string(
        self,
        schema: dict[str, Any],
        value: str,
        path: str,
        issues: list[ValidationIssue],
    ) -> None:
        self._check_length(schema, value, path, issues, "Characters")
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            try:
                matches = re.search(pattern, value)
            except re.error:
                issues.append(ValidationIssue(path, "invalid_schema", f"Invalid pattern configured for {path}."))
            else:
                if not matches:
                    issues.append(ValidationIssue(path, "pattern", f"Value at {path} has an invalid format."))

    def _validate_number(
        self,
        schema: dict[str, Any],
        value: int | float,
        path: str,
        issues: list[ValidationIssue],
    ) -> None:
        limits = (
            ("minimum", lambda current, limit: current >= limit, "greater than or equal to"),
            ("maximum", lambda current, limit: current <= limit, "less than or equal to"),
            ("exclusiveMinimum", lambda current, limit: current > limit, "greater than"),
            ("exclusiveMaximum", lambda current, limit: current < limit, "less than"),
        )
        for keyword, predicate, label in limits:
            limit = schema.get(keyword)
            if self._is_number(limit) and not predicate(value, limit):
                issues.append(ValidationIssue(path, keyword, f"Value at {path} must be {label} {limit}."))

    def _check_length(
        self,
        schema: dict[str, Any],
        value: str | list[Any],
        path: str,
        issues: list[ValidationIssue],
        label: str,
    ) -> None:
        suffix = "Length" if isinstance(value, str) else "Items"
        minimum = schema.get(f"min{suffix}")
        maximum = schema.get(f"max{suffix}")
        if isinstance(minimum, int) and len(value) < minimum:
            issues.append(ValidationIssue(path, f"min{suffix}", f"{label} at {path} must be at least {minimum}."))
        if isinstance(maximum, int) and len(value) > maximum:
            issues.append(ValidationIssue(path, f"max{suffix}", f"{label} at {path} must be at most {maximum}."))

    def _resolve_schema(
        self,
        schema: dict[str, Any],
        root_schema: dict[str, Any],
        path: str,
        issues: list[ValidationIssue],
        resolving_refs: set[str],
    ) -> dict[str, Any] | None:
        reference = schema.get("$ref")
        if not reference:
            return schema
        if not isinstance(reference, str) or not reference.startswith("#/") or reference in resolving_refs:
            issues.append(ValidationIssue(path, "invalid_schema", f"Unsupported schema reference at {path}."))
            return None
        target: Any = root_schema
        try:
            for token in reference[2:].split("/"):
                decoded = token.replace("~1", "/").replace("~0", "~")
                target = target[decoded]
        except (KeyError, TypeError):
            issues.append(ValidationIssue(path, "invalid_schema", f"Unresolved schema reference at {path}."))
            return None
        if not isinstance(target, dict):
            issues.append(ValidationIssue(path, "invalid_schema", f"Invalid schema reference at {path}."))
            return None
        resolving_refs.add(reference)
        merged = {**target, **{key: value for key, value in schema.items() if key != "$ref"}}
        if "$ref" in merged:
            return self._resolve_schema(merged, root_schema, path, issues, resolving_refs)
        return merged

    @staticmethod
    def _matches_type(value: Any, expected: str) -> bool:
        if expected == "null":
            return value is None
        if expected == "object":
            return isinstance(value, dict)
        if expected == "array":
            return isinstance(value, list)
        if expected == "string":
            return isinstance(value, str)
        if expected == "boolean":
            return isinstance(value, bool)
        if expected == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected == "number":
            return ToolArgumentValidator._is_number(value)
        return False

    @staticmethod
    def _is_number(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    @staticmethod
    def _format_types(types: list[str]) -> str:
        if len(types) <= 1:
            return types[0] if types else "a valid value"
        return " or ".join(types)

    @staticmethod
    def _child_path(path: str, key: str) -> str:
        return key if path == "$" else f"{path}.{key}"


tool_argument_validator = ToolArgumentValidator()
