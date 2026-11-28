import { Clock, User } from "lucide-react";
import { CourseImage } from "~/components/course-image";
import { cn, pluralize } from "~/lib/utils";
import type { FeaturedCourse } from "~/modules/landing-page/index.server";

/** How many modules the card lists before it folds the rest into "and N more". */
const MODULE_LIMIT = 4;

// The covers behind the course card, each a step further out from behind it,
// fanned to its upper right like a deck. Anchored to the top-right corner of
// the main card, pivoting on their own bottom-right corner.
const COVER_POSES = [
  { width: "w-[85%]", pose: "rotate-[5deg] top-6" },
  { width: "w-[78%]", pose: "rotate-[10deg] top-3" },
];

function StackedCover({
  course,
  step,
}: {
  course: FeaturedCourse;
  step: number;
}) {
  const { width, pose } = COVER_POSES[step];
  return (
    <div
      aria-hidden
      className={cn(
        "absolute right-0 origin-bottom-right overflow-hidden rounded-xl border border-border bg-card shadow-md",
        width,
        pose
      )}
    >
      <CourseImage
        src={course.coverImageUrl}
        alt=""
        className="aspect-video w-full object-cover"
      />
    </div>
  );
}

function lessonsLabel(count: number) {
  return `${count} ${pluralize(count, "lesson")}`;
}

function ModuleOutline({ modules }: { modules: FeaturedCourse["modules"] }) {
  const shown = modules.slice(0, MODULE_LIMIT);
  const rest = modules.length - shown.length;

  if (shown.length === 0) return null;

  return (
    <div className="mt-4 border-t border-border pt-3">
      <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        What you&apos;ll learn
      </p>
      <ol className="mt-2 divide-y divide-border">
        {shown.map((module, index) => (
          <li key={module.id} className="flex items-center gap-3 py-2 text-sm">
            <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-muted text-xs font-medium text-muted-foreground">
              {index + 1}
            </span>
            <span className="flex-1 truncate">{module.title}</span>
            <span className="whitespace-nowrap text-xs text-muted-foreground">
              {lessonsLabel(module.lessonCount)}
            </span>
          </li>
        ))}
      </ol>
      {rest > 0 && (
        <p className="pt-2 text-xs text-muted-foreground">
          and {rest} more {pluralize(rest, "module")}
        </p>
      )}
    </div>
  );
}

function SelfPacedBadge() {
  return (
    <div className="mb-3 flex w-fit items-center gap-3 rounded-lg border border-border bg-card px-4 py-3 shadow-lg">
      <span className="flex size-9 items-center justify-center rounded-md bg-primary/10">
        <Clock className="size-5 text-primary" />
      </span>
      <div className="text-sm">
        <p className="font-medium">Self-paced</p>
        <p className="text-muted-foreground">Learn on your schedule</p>
      </div>
    </div>
  );
}

// No overflow-hidden: the badge hangs over the card's left edge, so the cover
// rounds its own top corners instead.
function PreviewCourseCard({ course }: { course: FeaturedCourse }) {
  return (
    <div className="relative mt-16 rounded-xl border border-border bg-card shadow-xl">
      <CourseImage
        src={course.coverImageUrl}
        alt={course.title}
        className="aspect-video w-full rounded-t-xl object-cover"
      />
      <div className="p-5 sm:p-6">
        {/* Straddles the cover's bottom edge, so it never covers the outline.
            Positioned, or the inline-level cover paints over it. */}
        <div className="relative -ml-9 -mt-16 sm:-ml-10">
          <SelfPacedBadge />
        </div>
        <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          {course.categoryName} · {lessonsLabel(course.lessonCount)}
        </p>
        <h2 className="mt-2 text-xl font-semibold leading-snug">
          {course.title}
        </h2>
        <p className="mt-2 flex items-center gap-1.5 text-sm text-muted-foreground">
          <User className="size-4" />
          {course.instructorName}
        </p>
        <ModuleOutline modules={course.modules} />
      </div>
    </div>
  );
}

/**
 * The hero's product shot: the first course as a card, with the next two
 * courses' covers stacked behind it and the self-paced promise pinned on.
 */
export function CoursePreview({
  courses,
}: {
  courses: ReadonlyArray<FeaturedCourse>;
}) {
  const [course, ...behind] = courses;

  return (
    // `isolate` gives the glow a stacking context to sit at the back of.
    // Without it, -z-10 drops it behind the page's own background, where
    // it never paints.
    <div className="relative isolate mx-auto w-full max-w-lg pr-6 sm:pr-10">
      <div
        aria-hidden
        className="absolute -inset-8 -z-10 rounded-full bg-primary/10 blur-3xl"
      />
      {behind.slice(0, COVER_POSES.length).map((cover, step) => (
        <StackedCover key={cover.id} course={cover} step={step} />
      ))}
      <PreviewCourseCard course={course} />
    </div>
  );
}
