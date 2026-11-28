import type { Route } from "./+types/home";
import { eq } from "drizzle-orm";
import { db } from "~/db";
import { users } from "~/db/schema";
import { DevUI } from "~/components/dev-ui";
import { SiteHeader } from "~/components/landing/site-header";
import { LandingHero } from "~/components/landing/hero";
import { StatsBand } from "~/components/landing/stats-band";
import { FeaturedCourses } from "~/components/landing/featured-courses";
import { SiteFooter } from "~/components/landing/site-footer";
import { landingPage } from "~/modules/landing-page/index.server";
import { getCurrentUserId, getDevCountry } from "~/lib/session";
import { getCountryTierInfo, COUNTRIES } from "~/lib/ppp";

function getAllUsers() {
  return db.select().from(users).all();
}

function getUserById(id: number) {
  return db.select().from(users).where(eq(users.id, id)).get();
}

export function meta() {
  return [
    { title: "Cadence — Learn at your own pace" },
    {
      name: "description",
      content:
        "A modern course platform for developers. Browse courses, track your progress, and learn at your own pace.",
    },
  ];
}

export async function loader({ request }: Route.LoaderArgs) {
  const page = landingPage.get();
  const devUsers = getAllUsers();
  const currentUserId = await getCurrentUserId(request);
  const currentUser = currentUserId ? getUserById(currentUserId) : null;
  const devCountry = await getDevCountry(request);

  return {
    ...page,
    users: devUsers.map((u) => ({ id: u.id, name: u.name, role: u.role })),
    currentUser: currentUser
      ? { id: currentUser.id, name: currentUser.name, role: currentUser.role }
      : null,
    devCountry,
    countryTierInfo: getCountryTierInfo(devCountry),
    countries: COUNTRIES,
  };
}

export default function Home({ loaderData }: Route.ComponentProps) {
  const {
    featuredCourses,
    stats,
    users,
    currentUser,
    devCountry,
    countryTierInfo,
    countries,
  } = loaderData;

  return (
    <div className="min-h-screen bg-background">
      <SiteHeader signedIn={currentUser !== null} />
      <main>
        <LandingHero featuredCourses={featuredCourses} />
        <StatsBand stats={stats} />
        {featuredCourses.length > 0 && (
          <FeaturedCourses courses={featuredCourses} />
        )}
      </main>
      <SiteFooter signedIn={currentUser !== null} />
      <DevUI
        users={users}
        currentUser={currentUser}
        devCountry={devCountry}
        countryTierInfo={countryTierInfo}
        countries={countries}
      />
    </div>
  );
}
