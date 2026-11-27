import {
  type CollapsedSections,
  expansionToggleLabel,
  NO_COLLAPSED_SECTIONS,
  outlineExpansion,
  type OutlineExpansionAction,
} from "./lib/expansion";
import {
  buildCourseOutline,
  type CourseOutline,
  type LessonStatus,
  type OutlineInput,
  type OutlineLesson,
  type OutlineSection,
  outlineSummary,
  sectionSummary,
} from "./lib/outline";

export type {
  CollapsedSections,
  CourseOutline,
  LessonStatus,
  OutlineExpansionAction,
  OutlineInput,
  OutlineLesson,
  OutlineSection,
};

export {
  buildCourseOutline,
  expansionToggleLabel,
  NO_COLLAPSED_SECTIONS,
  outlineExpansion,
  outlineSummary,
  sectionSummary,
};
