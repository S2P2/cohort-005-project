import { beforeEach, describe, expect, it } from "vitest";
import { eq } from "drizzle-orm";
import { createTestDb, seedBaseData, type TestDb } from "~/test/setup";
import * as schema from "~/db/schema";
import { CourseStatus, UserRole } from "~/db/schema";
import { markdown } from "~/lib/text";
import { createLandingPage } from "../index.server";

const DAY_MS = 86_400_000;
const NEWEST = new Date("2026-10-01T12:00:00.000Z");

let db: TestDb;
let landingPage: ReturnType<typeof createLandingPage>;
let instructorId: number;
let otherInstructorId: number;
let categoryId: number;

beforeEach(() => {
  db = createTestDb();
  const base = seedBaseData(db);
  instructorId = base.instructor.id;
  categoryId = base.category.id;
  otherInstructorId = addUser("Olive Other", UserRole.Instructor).id;
  // The seeded course is created "now", which would make it the newest
  // course in every test. Pin it to a hundred days before the fixtures.
  db.update(schema.courses)
    .set({
      createdAt: new Date(NEWEST.getTime() - 100 * DAY_MS).toISOString(),
    })
    .where(eq(schema.courses.id, base.course.id))
    .run();
  landingPage = createLandingPage(db);
});

// ─── Fixtures ───
// Nothing writes these tables from a service a test could call, so rows go in
// directly. Kept in one place so a schema change touches only this block.

let slugCounter = 0;

function addUser(name: string, role: UserRole) {
  return db
    .insert(schema.users)
    .values({
      name,
      email: `${name.toLowerCase().replaceAll(" ", ".")}@example.com`,
      role,
    })
    .returning()
    .get();
}

function addCourse({
  title,
  instructor = instructorId,
  status = CourseStatus.Published,
  daysOld = 0,
  coverImageUrl = null,
}: {
  title: string;
  instructor?: number;
  status?: CourseStatus;
  daysOld?: number;
  coverImageUrl?: string | null;
}) {
  slugCounter += 1;
  return db
    .insert(schema.courses)
    .values({
      title,
      slug: `course-${slugCounter}`,
      description: `About ${title}`,
      salesCopy: markdown(`## ${title}`),
      instructorId: instructor,
      categoryId,
      status,
      coverImageUrl,
      createdAt: new Date(NEWEST.getTime() - daysOld * DAY_MS).toISOString(),
    })
    .returning()
    .get();
}

function addModule(courseId: number, title: string, position: number) {
  return db
    .insert(schema.modules)
    .values({ courseId, title, position })
    .returning()
    .get();
}

function addLesson(moduleId: number, title: string, position: number) {
  return db
    .insert(schema.lessons)
    .values({ moduleId, title, position })
    .returning()
    .get();
}

/** A module titled `title` holding `lessonCount` lessons, named "Lesson 1", "Lesson 2"… */
function addModuleWithLessons(
  courseId: number,
  {
    title,
    position,
    lessonCount,
  }: { title: string; position: number; lessonCount: number }
) {
  const module = addModule(courseId, title, position);
  for (let i = 1; i <= lessonCount; i += 1) {
    addLesson(module.id, `Lesson ${i}`, i);
  }
  return module;
}

describe("landingPage.get()", () => {
  it("features the three newest published courses, newest first", () => {
    const oldest = addCourse({ title: "Oldest", daysOld: 30 });
    addCourse({ title: "Middle", daysOld: 20 });
    addCourse({
      title: "Newest",
      daysOld: 0,
      coverImageUrl: "/covers/newest.png",
    });
    addCourse({ title: "Fourth", daysOld: 10 });

    const { featuredCourses } = landingPage.get();

    expect(featuredCourses.map((course) => course.title)).toEqual([
      "Newest",
      "Fourth",
      "Middle",
    ]);
    expect(oldest.id).not.toBe(featuredCourses.map((course) => course.id));
    expect(featuredCourses[0]).toMatchObject({
      slug: "course-3",
      description: "About Newest",
      coverImageUrl: "/covers/newest.png",
      instructorName: "Test Instructor",
      categoryName: "Programming",
    });
  });

  it("puts the newer course first when two share a creation date", () => {
    addCourse({ title: "Earlier import", daysOld: 5 });
    addCourse({ title: "Later import", daysOld: 5 });

    const { featuredCourses } = landingPage.get();

    expect(featuredCourses.map((course) => course.title)).toEqual([
      "Later import",
      "Earlier import",
      "Test Course",
    ]);
  });

  it("leaves draft and archived courses out of the featured list", () => {
    addCourse({ title: "Draft", status: CourseStatus.Draft, daysOld: 0 });
    addCourse({ title: "Archived", status: CourseStatus.Archived, daysOld: 1 });

    const { featuredCourses } = landingPage.get();

    expect(featuredCourses.map((course) => course.title)).toEqual([
      "Test Course",
    ]);
  });

  it("gives each featured course its modules in order, with lesson counts", () => {
    const newest = addCourse({ title: "Newest", daysOld: 0 });
    addModuleWithLessons(newest.id, {
      title: "Second module",
      position: 2,
      lessonCount: 1,
    });
    addModuleWithLessons(newest.id, {
      title: "First module",
      position: 1,
      lessonCount: 3,
    });
    addModule(newest.id, "Empty module", 3);
    const second = addCourse({ title: "Second", daysOld: 1 });
    addModuleWithLessons(second.id, {
      title: "Only module",
      position: 1,
      lessonCount: 2,
    });

    const { featuredCourses } = landingPage.get();

    expect(featuredCourses.map((course) => course.title)).toEqual([
      "Newest",
      "Second",
      "Test Course",
    ]);
    expect(featuredCourses[0].modules).toMatchObject([
      { title: "First module", lessonCount: 3 },
      { title: "Second module", lessonCount: 1 },
      { title: "Empty module", lessonCount: 0 },
    ]);
    expect(featuredCourses[0].lessonCount).toBe(4);
    expect(featuredCourses[1].modules).toMatchObject([
      { title: "Only module", lessonCount: 2 },
    ]);
    expect(featuredCourses[1].lessonCount).toBe(2);
    expect(featuredCourses[2].modules).toEqual([]);
    expect(featuredCourses[2].lessonCount).toBe(0);
  });

  it("counts courses, instructors and lessons across every published course", () => {
    const first = addCourse({ title: "First", daysOld: 1 });
    addModuleWithLessons(first.id, {
      title: "Module",
      position: 1,
      lessonCount: 3,
    });
    const second = addCourse({
      title: "Second",
      instructor: otherInstructorId,
      daysOld: 2,
    });
    addModuleWithLessons(second.id, {
      title: "Module",
      position: 1,
      lessonCount: 5,
    });
    // A fourth published course, past the featured three, still counts.
    addCourse({ title: "Fourth", instructor: otherInstructorId, daysOld: 3 });
    const draft = addCourse({ title: "Draft", status: CourseStatus.Draft });
    addModuleWithLessons(draft.id, {
      title: "Module",
      position: 1,
      lessonCount: 100,
    });

    const { stats } = landingPage.get();

    expect(stats).toEqual({
      courseCount: 4,
      instructorCount: 2,
      lessonCount: 8,
    });
  });

  it("returns an empty page rather than throwing on an empty catalogue", () => {
    const empty = createLandingPage(createTestDb());

    expect(empty.get()).toEqual({
      featuredCourses: [],
      stats: { courseCount: 0, instructorCount: 0, lessonCount: 0 },
    });
  });
});
