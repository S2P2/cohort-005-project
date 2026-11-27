import { LessonProgressStatus } from "~/db/schema";
import { formatDuration } from "~/lib/utils";

/** How far a Student has got with a lesson, as the course page shows it. */
export type LessonStatus = "not-started" | "in-progress" | "completed";

export type OutlineLesson = {
  id: number;
  title: string;
  durationMinutes: number | null;
  /** Short, like "12m" or "1h 5m". Null when the duration is unknown. */
  durationLabel: string | null;
  status: LessonStatus;
};

export type OutlineSection = {
  id: number;
  title: string;
  lessons: ReadonlyArray<OutlineLesson>;
  lessonCount: number;
  /** The lessons' durations added up; a lesson without one counts as 0. */
  durationMinutes: number;
  /** Short, like "35m". Null when no lesson has a duration. */
  durationLabel: string | null;
  completedCount: number;
};

export type CourseOutline = {
  sections: ReadonlyArray<OutlineSection>;
  sectionCount: number;
  lessonCount: number;
  durationMinutes: number;
  durationLabel: string | null;
  completedCount: number;
};

export type OutlineInput = {
  /** A course's modules, in the order the page lists them. */
  modules: ReadonlyArray<{
    id: number;
    title: string;
    lessons: ReadonlyArray<
      Pick<OutlineLesson, "id" | "title" | "durationMinutes">
    >;
  }>;
  /** Lesson id to its `LessonProgressStatus`, for the Person viewing. */
  lessonProgressMap: Readonly<Record<number, string>>;
};

function durationLabel(minutes: number): string | null {
  return minutes > 0 ? formatDuration(minutes, true, false, false) : null;
}

function lessonStatus(recorded: string | undefined): LessonStatus {
  switch (recorded) {
    case LessonProgressStatus.Completed:
      return "completed";
    case LessonProgressStatus.InProgress:
      return "in-progress";
    default:
      return "not-started";
  }
}

function outlineLesson(
  lesson: Pick<OutlineLesson, "id" | "title" | "durationMinutes">,
  lessonProgressMap: Readonly<Record<number, string>>
): OutlineLesson {
  return {
    ...lesson,
    durationLabel: durationLabel(lesson.durationMinutes ?? 0),
    status: lessonStatus(lessonProgressMap[lesson.id]),
  };
}

function sumDuration(lessons: ReadonlyArray<OutlineLesson>): number {
  return lessons.reduce(
    (sum, lesson) => sum + (lesson.durationMinutes ?? 0),
    0
  );
}

function countByStatus(
  lessons: ReadonlyArray<OutlineLesson>,
  status: LessonStatus
): number {
  return lessons.filter((lesson) => lesson.status === status).length;
}

/**
 * Everything the course content list shows, worked out once: each module
 * with its lessons and their totals, and the totals for the whole course.
 */
export function buildCourseOutline({
  modules,
  lessonProgressMap,
}: OutlineInput): CourseOutline {
  const sections = modules.map((module) => {
    const lessons = module.lessons.map((lesson) =>
      outlineLesson(lesson, lessonProgressMap)
    );
    const durationMinutes = sumDuration(lessons);
    return {
      id: module.id,
      title: module.title,
      lessons,
      lessonCount: lessons.length,
      durationMinutes,
      durationLabel: durationLabel(durationMinutes),
      completedCount: countByStatus(lessons, "completed"),
    };
  });

  const lessons = sections.flatMap((section) => [...section.lessons]);
  const durationMinutes = sumDuration(lessons);

  return {
    sections,
    sectionCount: sections.length,
    lessonCount: lessons.length,
    durationMinutes,
    durationLabel: durationLabel(durationMinutes),
    completedCount: countByStatus(lessons, "completed"),
  };
}

function count(n: number, noun: string): string {
  return `${n} ${n === 1 ? noun : `${noun}s`}`;
}

/** The parts of a summary that are present, joined with a middle dot. */
function summary(parts: ReadonlyArray<string | null>): string {
  const present = parts.filter((part): part is string => part !== null);
  return present.join(" · ");
}

/** The line under "Course Content": "3 modules · 9 lessons · 1h 54m". */
export function outlineSummary(
  outline: Pick<CourseOutline, "sectionCount" | "lessonCount" | "durationLabel">
): string {
  return summary([
    count(outline.sectionCount, "module"),
    count(outline.lessonCount, "lesson"),
    outline.durationLabel,
  ]);
}

/** The line beside a module's title: "3 lessons · 35m · 2/3 completed". */
export function sectionSummary(
  section: Pick<
    OutlineSection,
    "lessonCount" | "durationLabel" | "completedCount"
  >,
  { showProgress }: { showProgress: boolean }
): string {
  return summary([
    count(section.lessonCount, "lesson"),
    section.durationLabel,
    showProgress
      ? `${section.completedCount}/${section.lessonCount} completed`
      : null,
  ]);
}
