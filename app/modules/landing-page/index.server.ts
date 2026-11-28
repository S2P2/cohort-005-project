import { db } from "~/db";
import { getLandingPage, type Db } from "./lib/landing-page";

export type {
  CourseModule,
  FeaturedCourse,
  LandingPage,
  LandingStats,
} from "./lib/landing-page";

/**
 * The landing page bound to a database. The app uses `landingPage`, which
 * reads the app database; tests build their own against a test database.
 */
export function createLandingPage(database: Db) {
  return {
    /**
     * Everything the landing page shows: the newest published courses, with
     * their modules, and the platform figures for its stats band.
     */
    get() {
      return getLandingPage(database);
    },
  };
}

// Pure so a route importing this only for its loader doesn't pull the
// database into the client bundle (see `~/db`).
export const landingPage = /* @__PURE__ */ createLandingPage(db);
