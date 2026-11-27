import { describe, expect, it } from "vitest";
import {
  expansionToggleLabel,
  NO_COLLAPSED_SECTIONS,
  outlineExpansion,
} from "../expansion";

const SECTION_IDS = [11, 12, 13] as const;

function collapsedFrom(ids: ReadonlyArray<number>) {
  return new Set(ids);
}

describe("outlineExpansion", () => {
  it("starts with nothing collapsed", () => {
    expect(NO_COLLAPSED_SECTIONS.size).toBe(0);
  });

  it("collapses a section that is open", () => {
    const next = outlineExpansion(NO_COLLAPSED_SECTIONS, {
      type: "toggle",
      sectionId: 12,
    });

    expect([...next]).toEqual([12]);
  });

  it("expands a section that is collapsed", () => {
    const next = outlineExpansion(collapsedFrom([11, 12]), {
      type: "toggle",
      sectionId: 12,
    });

    expect([...next]).toEqual([11]);
  });

  it("leaves the previous state alone", () => {
    const previous = collapsedFrom([11]);

    outlineExpansion(previous, { type: "toggle", sectionId: 12 });
    outlineExpansion(previous, { type: "toggleAll", sectionIds: SECTION_IDS });

    expect([...previous]).toEqual([11]);
  });

  it("collapses every section when none is collapsed", () => {
    const next = outlineExpansion(NO_COLLAPSED_SECTIONS, {
      type: "toggleAll",
      sectionIds: SECTION_IDS,
    });

    expect([...next]).toEqual([11, 12, 13]);
  });

  it("expands every section when any is collapsed", () => {
    const next = outlineExpansion(collapsedFrom([12]), {
      type: "toggleAll",
      sectionIds: SECTION_IDS,
    });

    expect(next.size).toBe(0);
  });
});

describe("expansionToggleLabel", () => {
  it("offers to collapse while everything is open", () => {
    expect(expansionToggleLabel(NO_COLLAPSED_SECTIONS)).toBe("Collapse all");
  });

  it("offers to expand once anything is collapsed", () => {
    expect(expansionToggleLabel(collapsedFrom([13]))).toBe("Expand all");
  });
});
