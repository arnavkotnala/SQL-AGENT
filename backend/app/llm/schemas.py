"""Structured-output contracts between the agent and the language model."""
from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field


class GoalInterpretation(BaseModel):
    interpreted_goal: str = Field(description="One-sentence restatement of what the user wants.")
    operation: Literal["read", "modify"] = Field(
        description="'read' to retrieve/analyse data; 'modify' only when the user explicitly asks to change, "
                    "update, add or delete data in their spreadsheet.")
    feasible: bool = Field(description="False if the connected data cannot answer this request.")
    infeasible_reason: Optional[str] = Field(default=None, description="Why it is not feasible, if not.")
    needs_clarification: bool = Field(
        description="True only if the request is genuinely ambiguous in a way that changes the result.")
    clarification_question: Optional[str] = None
    success_criteria: list[str] = Field(description="Concrete, checkable conditions for a correct result.")
    requested_columns: list[str] = Field(description="Fields the user wants in the output, in plain words.")
    relevant_tables: list[str] = Field(description="Tables from the schema that are likely needed.")


class SqlParam(BaseModel):
    name: str = Field(description="Parameter name without the colon, as used in the SQL (e.g. threshold).")
    value: Optional[Union[str, float, bool]] = Field(
        description="Literal value bound to the parameter. Use a JSON number (50, 0.5) for numeric comparisons, "
                    "never a quoted string such as \"50\".")


class NewColumn(BaseModel):
    name: str = Field(description="snake_case SQL column name to add to the sheet table.")
    header: str = Field(description="Header text to write in the spreadsheet.")
    type: Literal["TEXT", "INTEGER", "REAL", "DATE"] = "TEXT"


class SqlProposal(BaseModel):
    diagnosis: Optional[str] = Field(default=None,
                                     description="If a previous attempt failed, what went wrong and how this fixes it.")
    plan: list[str] = Field(description="Short ordered steps of the query logic (joins, filters, aggregation).")
    sql: str = Field(description="Exactly one SQL statement using named :params for literal user values.")
    params: list[SqlParam] = Field(default_factory=list)
    expected_columns: list[str] = Field(default_factory=list, description="Output column aliases, in order.")
    new_columns: list[NewColumn] = Field(
        default_factory=list,
        description="Modify mode only: columns that must be added to the sheet before the statement runs.")
    rationale: str = Field(description="Why this statement satisfies the goal.")

    def params_dict(self) -> dict[str, Any]:
        return {p.name.lstrip(":"): p.value for p in self.params}


class ColumnFormat(BaseModel):
    column: str
    label: str = Field(description="Human-friendly header for the export.")
    kind: Literal["text", "integer", "decimal", "percent_0_100", "percent_fraction", "date", "datetime", "currency"]


class ResultEvaluation(BaseModel):
    satisfied: bool = Field(description="True if the result fully meets the goal and success criteria.")
    issues: list[str] = Field(default_factory=list)
    refinement_instructions: Optional[str] = Field(
        default=None, description="If not satisfied: precise instructions for the next query.")
    summary: str = Field(description="2-3 sentence plain-language summary of the result for the user.")
    column_formats: list[ColumnFormat] = Field(default_factory=list)


def to_gemini_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema -> self-contained schema Gemini accepts (refs inlined, titles removed,
    Optional[...] expressed as type arrays)."""
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            node = {k: walk(v) for k, v in node.items() if k not in ("title", "default")}
            any_of = node.get("anyOf")
            if any_of and all(set(a) <= {"type"} for a in any_of):
                types = [a["type"] for a in any_of]
                node.pop("anyOf")
                node["type"] = types
            elif any_of and len(any_of) == 2 and {"type": "null"} in any_of:
                other = next(a for a in any_of if a != {"type": "null"})
                node.pop("anyOf")
                merged = dict(other)
                t = merged.get("type")
                merged["type"] = [t, "null"] if isinstance(t, str) else t
                node.update(merged)
            return node
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(raw)
