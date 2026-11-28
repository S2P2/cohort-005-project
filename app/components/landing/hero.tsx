import { Link } from "react-router";
import { ArrowRight, Clock } from "lucide-react";
import { Button } from "~/components/ui/button";
import { CoursePreview } from "~/components/landing/course-preview";
import type { FeaturedCourse } from "~/modules/landing-page/index.server";

function HeroCopy() {
  return (
    <div>
      <span className="inline-flex items-center gap-2 rounded-full border border-border bg-card px-3 py-1 text-xs font-medium text-muted-foreground">
        <Clock className="size-3.5" />
        Self-paced video courses
      </span>
      <h1 className="mt-6 text-4xl font-bold tracking-tight text-balance sm:text-5xl lg:text-6xl">
        <span className="block">Learn to code,</span>
        <span className="block text-muted-foreground">at your own pace</span>
      </h1>
      <p className="mt-6 max-w-xl text-lg text-muted-foreground">
        Structured courses built by experienced instructors. Track your
        progress, take quizzes, and build real-world skills.
      </p>
      <div className="mt-8 flex flex-wrap items-center gap-3">
        <Button asChild size="lg">
          <Link to="/courses">
            Browse Courses
            <ArrowRight className="ml-2 size-4" />
          </Link>
        </Button>
        <Button asChild size="lg" variant="outline">
          <Link to="/signup">Create an account</Link>
        </Button>
      </div>
    </div>
  );
}

export function LandingHero({
  featuredCourses,
}: {
  featuredCourses: ReadonlyArray<FeaturedCourse>;
}) {
  return (
    <section className="overflow-hidden">
      <div className="mx-auto grid max-w-6xl gap-14 px-4 pb-20 pt-14 sm:px-6 sm:pt-20 lg:grid-cols-2 lg:items-center lg:gap-10">
        <HeroCopy />
        {featuredCourses.length > 0 && (
          <CoursePreview courses={featuredCourses} />
        )}
      </div>
    </section>
  );
}
