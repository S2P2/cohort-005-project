/**
 * Which sections a Person has collapsed, by module id. Everything else is
 * open, so a course starts out fully expanded and the set starts out empty.
 */
export type CollapsedSections = ReadonlySet<number>;

export const NO_COLLAPSED_SECTIONS: CollapsedSections = new Set();

export type OutlineExpansionAction =
  | { type: "toggle"; sectionId: number }
  /** Collapses every section; when any is already collapsed, expands them all instead. */
  | { type: "toggleAll"; sectionIds: ReadonlyArray<number> };

export function outlineExpansion(
  collapsed: CollapsedSections,
  action: OutlineExpansionAction
): CollapsedSections {
  switch (action.type) {
    case "toggle": {
      const next = new Set(collapsed);
      if (next.has(action.sectionId)) {
        next.delete(action.sectionId);
      } else {
        next.add(action.sectionId);
      }
      return next;
    }
    case "toggleAll":
      return collapsed.size > 0 ? new Set() : new Set(action.sectionIds);
  }
}

/** What the all-sections button offers: the opposite of the current state. */
export function expansionToggleLabel(collapsed: CollapsedSections): string {
  return collapsed.size > 0 ? "Expand all" : "Collapse all";
}
