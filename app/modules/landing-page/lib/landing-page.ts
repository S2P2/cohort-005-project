import { count, countDistinct, desc, eq, inArray } from "drizzle-orm";
import type { BetterSQLite3Database } from "drizzle-orm/better-sqlite3";
import * as schema from "~/db/schema";
import {
  CourseStatus,
  categories,
  courses,
  lessons,
  modules,
  users,
} from "~/db/schema";

// ─── Landing page ───
// Everything the landing page shows, computed in one call: the courses it
// features and the platform figures in its stats band. Both look at published
// courses only, since a visitor cannot buy a draft or an archived course.

export type Db = BetterSQLite3Database<typeof schema>;

export const FEATURED_COURSE_LIMIT = 3;

export type CourseModule = {
  id: number;
  title: string;
  lessonCount: number;
};

export type FeaturedCourse = {
  id: number;
  title: string;
  slug: string;
  description: string;
  instructorName: string;
  categoryName: string;
  coverImageUrl: string | null;
  /** Every lesson in the course, added up over its modules. */
  lessonCount: number;
  /** In the order the course lists them. Empty for a course with no modules. */
  modules: ReadonlyArray<CourseModule>;
};

export type LandingStats = {
  /** Published courses. */
  courseCount: number;
  /** Instructors with at least one published course. */
  instructorCount: number;
  /** Lessons across every published course. */
  lessonCount: number;
};

export type LandingPage = {
  /** The newest published courses, newest first. At most three. */
  featuredCourses: ReadonlyArray<FeaturedCourse>;
  stats: LandingStats;
};

function newestPublishedCourses(db: Db) {
  return (
    db
      .select({
        id: courses.id,
        title: courses.title,
        slug: courses.slug,
        description: courses.description,
        coverImageUrl: courses.coverImageUrl,
        instructorName: users.name,
        categoryName: categories.name,
      })
      .from(courses)
      .innerJoin(users, eq(courses.instructorId, users.id))
      .innerJoin(categories, eq(courses.categoryId, categories.id))
      .where(eq(courses.status, CourseStatus.Published))
      // The id breaks ties: two courses can share a creation date, and the
      // order must not depend on how SQLite happens to return the rows.
      .orderBy(desc(courses.createdAt), desc(courses.id))
      .limit(FEATURED_COURSE_LIMIT)
      .all()
  );
}

/** The modules of each given course, in course order, as one query. */
function modulesByCourse(db: Db, courseIds: ReadonlyArray<number>) {
  const byCourse = new Map<number, CourseModule[]>();
  const rows = db
    .select({
      id: modules.id,
      courseId: modules.courseId,
      title: modules.title,
      lessonCount: count(lessons.id),
    })
    .from(modules)
    .leftJoin(lessons, eq(lessons.moduleId, modules.id))
    .where(inArray(modules.courseId, [...courseIds]))
    .groupBy(modules.id)
    .orderBy(modules.courseId, modules.position)
    .all();

  for (const row of rows) {
    const courseModules = byCourse.get(row.courseId) ?? [];
    courseModules.push({
      id: row.id,
      title: row.title,
      lessonCount: row.lessonCount,
    });
    byCourse.set(row.courseId, courseModules);
  }

  return byCourse;
}

function publishedStats(db: Db): LandingStats {
  const catalogue = db
    .select({
      courseCount: count(),
      instructorCount: countDistinct(courses.instructorId),
    })
    .from(courses)
    .where(eq(courses.status, CourseStatus.Published))
    .get();

  const curriculum = db
    .select({ lessonCount: count(lessons.id) })
    .from(lessons)
    .innerJoin(modules, eq(lessons.moduleId, modules.id))
    .innerJoin(courses, eq(modules.courseId, courses.id))
    .where(eq(courses.status, CourseStatus.Published))
    .get();

  return {
    courseCount: catalogue?.courseCount ?? 0,
    instructorCount: catalogue?.instructorCount ?? 0,
    lessonCount: curriculum?.lessonCount ?? 0,
  };
}

export function getLandingPage(db: Db): LandingPage {
  const newest = newestPublishedCourses(db);
  const outlineByCourse = modulesByCourse(
    db,
    newest.map((course) => course.id)
  );

  const featuredCourses = newest.map((course) => {
    const outline = outlineByCourse.get(course.id) ?? [];
    return {
      ...course,
      modules: outline,
      lessonCount: outline.reduce(
        (total, module) => total + module.lessonCount,
        0
      ),
    };
  });

  return { featuredCourses, stats: publishedStats(db) };
}
