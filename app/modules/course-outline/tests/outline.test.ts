import { describe, expect, it } from "vitest";
import { LessonProgressStatus } from "~/db/schema";
import {
  buildCourseOutline,
  outlineSummary,
  sectionSummary,
  type OutlineInput,
} from "../outline";

const FOUNDATIONS = 11;
const COMPONENTS = 12;

function course(): OutlineInput {
  return {
    modules: [
      {
        id: FOUNDATIONS,
        title: "Foundations",
        lessons: [
          { id: 1, title: "What a Design System Is For", durationMinutes: 9 },
          { id: 2, title: "Design Tokens", durationMinutes: 14 },
          { id: 3, title: "Spacing and Type Scales", durationMinutes: null },
        ],
      },
      {
        id: COMPONENTS,
        title: "Components",
        lessons: [
          { id: 4, title: "Variants and Composition", durationMinutes: 60 },
          { id: 5, title: "Accessible Primitives", durationMinutes: 16 },
        ],
      },
    ],
    lessonProgressMap: {
      1: LessonProgressStatus.Completed,
      2: LessonProgressStatus.InProgress,
      3: LessonProgressStatus.NotStarted,
      4: LessonProgressStatus.Completed,
    },
  };
}

describe("buildCourseOutline", () => {
  it("lists each module with its lessons in order", () => {
    const outline = buildCourseOutline(course());

    expect(
      outline.sections.map((section) => [section.id, section.title])
    ).toEqual([
      [FOUNDATIONS, "Foundations"],
      [COMPONENTS, "Components"],
    ]);
    expect(outline.sections[0].lessons.map((lesson) => lesson.id)).toEqual([
      1, 2, 3,
    ]);
    expect(outline.sections[1].lessons[1].title).toBe("Accessible Primitives");
  });

  it("adds up each module's duration and counts its lessons", () => {
    const outline = buildCourseOutline(course());

    expect(outline.sections[0]).toMatchObject({
      lessonCount: 3,
      durationMinutes: 23,
      durationLabel: "23m",
    });
    expect(outline.sections[1]).toMatchObject({
      lessonCount: 2,
      durationMinutes: 76,
      durationLabel: "1h 16m",
    });
  });

  it("totals the whole course", () => {
    const outline = buildCourseOutline(course());

    expect(outline).toMatchObject({
      sectionCount: 2,
      lessonCount: 5,
      durationMinutes: 99,
      durationLabel: "1h 39m",
      completedCount: 2,
    });
  });

  it("labels each lesson's duration, and leaves one unknown unlabelled", () => {
    const outline = buildCourseOutline(course());
    const lessons = outline.sections.flatMap((section) => [...section.lessons]);

    expect(lessons.map((lesson) => lesson.durationLabel)).toEqual([
      "9m",
      "14m",
      null,
      "1h",
      "16m",
    ]);
  });

  it("reads each lesson's status from the progress map", () => {
    const outline = buildCourseOutline(course());
    const lessons = outline.sections.flatMap((section) => [...section.lessons]);

    expect(lessons.map((lesson) => lesson.status)).toEqual([
      LessonProgressStatus.Completed,
      LessonProgressStatus.InProgress,
      LessonProgressStatus.NotStarted,
      LessonProgressStatus.Completed,
      LessonProgressStatus.NotStarted,
    ]);
  });

  it("treats a status it does not know as not started", () => {
    const outline = buildCourseOutline({
      modules: [
        {
          id: 1,
          title: "Odd",
          lessons: [{ id: 7, title: "Seven", durationMinutes: 1 }],
        },
      ],
      lessonProgressMap: { 7: "something-else" },
    });

    expect(outline.sections[0].lessons[0].status).toBe(
      LessonProgressStatus.NotStarted
    );
    expect(outline.completedCount).toBe(0);
  });

  it("counts the completed lessons per module", () => {
    const outline = buildCourseOutline(course());

    expect(outline.sections.map((section) => section.completedCount)).toEqual([
      1, 1,
    ]);
  });

  it("works without a progress record for anyone", () => {
    const outline = buildCourseOutline({
      modules: course().modules,
      lessonProgressMap: {},
    });

    expect(
      outline.sections.every((section) => section.completedCount === 0)
    ).toBe(true);
    expect(outline.durationLabel).toBe("1h 39m");
  });

  it("describes a module whose lessons have no durations", () => {
    const outline = buildCourseOutline({
      modules: [
        {
          id: 1,
          title: "Unmeasured",
          lessons: [{ id: 1, title: "A", durationMinutes: null }],
        },
      ],
      lessonProgressMap: {},
    });

    expect(outline.sections[0]).toMatchObject({
      lessonCount: 1,
      durationMinutes: 0,
      durationLabel: null,
    });
    expect(outline.durationLabel).toBeNull();
  });

  it("describes an empty course", () => {
    const outline = buildCourseOutline({ modules: [], lessonProgressMap: {} });

    expect(outline).toEqual({
      sections: [],
      sectionCount: 0,
      lessonCount: 0,
      durationMinutes: 0,
      durationLabel: null,
      completedCount: 0,
    });
  });
});

describe("outlineSummary", () => {
  it("joins the modules, lessons and total duration", () => {
    expect(
      outlineSummary({
        sectionCount: 3,
        lessonCount: 9,
        durationLabel: "1h 54m",
      })
    ).toBe("3 modules · 9 lessons · 1h 54m");
  });

  it("uses the singular for one module and one lesson", () => {
    expect(
      outlineSummary({ sectionCount: 1, lessonCount: 1, durationLabel: "5m" })
    ).toBe("1 module · 1 lesson · 5m");
  });

  it("leaves the duration out when it is unknown", () => {
    expect(
      outlineSummary({ sectionCount: 2, lessonCount: 4, durationLabel: null })
    ).toBe("2 modules · 4 lessons");
  });
});

describe("sectionSummary", () => {
  const section = { lessonCount: 3, durationLabel: "35m", completedCount: 2 };

  it("gives the lessons and the duration", () => {
    expect(sectionSummary(section, { showProgress: false })).toBe(
      "3 lessons · 35m"
    );
  });

  it("adds the completed count for someone taking the course", () => {
    expect(sectionSummary(section, { showProgress: true })).toBe(
      "3 lessons · 35m · 2/3 completed"
    );
  });

  it("handles one lesson without a duration", () => {
    expect(
      sectionSummary(
        { lessonCount: 1, durationLabel: null, completedCount: 0 },
        { showProgress: true }
      )
    ).toBe("1 lesson · 0/1 completed");
  });

  it("says 0 lessons for an empty module", () => {
    expect(
      sectionSummary(
        { lessonCount: 0, durationLabel: null, completedCount: 0 },
        { showProgress: false }
      )
    ).toBe("0 lessons");
  });
});
