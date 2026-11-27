import { useId, useReducer } from "react";
import { Link } from "react-router";
import {
  CheckCircle2,
  ChevronDown,
  Circle,
  Pencil,
  PlayCircle,
} from "lucide-react";
import { Button } from "~/components/ui/button";
import { LessonProgressStatus } from "~/db/schema";
import { cn } from "~/lib/utils";
import {
  type CollapsedSections,
  expansionToggleLabel,
  NO_COLLAPSED_SECTIONS,
  outlineExpansion,
} from "~/modules/course-outline/expansion";
import {
  type CourseOutline,
  type OutlineLesson,
  type OutlineSection,
  outlineSummary,
  sectionSummary,
} from "~/modules/course-outline/outline";

/** How a lesson row behaves: the Instructor edits it, someone who can view
 * the course opens it, and everyone else only sees its title. */
type LessonMode = "edit" | "view" | "locked";

type CourseRef = { id: number; slug: string };

type CourseContentProps = {
  course: CourseRef;
  outline: CourseOutline;
  canView: boolean;
  canTake: boolean;
  isInstructor: boolean;
};

/**
 * The course's modules and lessons as one dense list: each module on a single
 * line with its lesson count, duration and progress, its lessons under it with
 * their durations in a column. Modules collapse, and all of them at once.
 */
export function CourseContent({
  course,
  outline,
  canView,
  canTake,
  isInstructor,
}: CourseContentProps) {
  const [collapsed, dispatch] = useReducer(
    outlineExpansion,
    NO_COLLAPSED_SECTIONS
  );
  const baseId = useId();
  const headingId = `${baseId}-heading`;
  const showProgress = canTake && !isInstructor;
  const mode: LessonMode = isInstructor ? "edit" : canView ? "view" : "locked";

  if (outline.sections.length === 0) {
    return <EmptyContent headingId={headingId} />;
  }

  return (
    <section aria-labelledby={headingId}>
      <ContentHeader
        headingId={headingId}
        outline={outline}
        collapsed={collapsed}
        onToggleAll={() =>
          dispatch({
            type: "toggleAll",
            sectionIds: outline.sections.map((section) => section.id),
          })
        }
      />
      <div className="divide-y overflow-hidden rounded-xl border bg-card">
        {outline.sections.map((section) => (
          <Section
            key={section.id}
            section={section}
            course={course}
            idPrefix={headingId}
            isCollapsed={collapsed.has(section.id)}
            onToggle={() => dispatch({ type: "toggle", sectionId: section.id })}
            mode={mode}
            showProgress={showProgress}
          />
        ))}
      </div>
    </section>
  );
}

/** A course that has no modules yet. */
function EmptyContent({ headingId }: { headingId: string }) {
  return (
    <section aria-labelledby={headingId}>
      <h2 id={headingId} className="mb-4 text-2xl font-bold">
        Course Content
      </h2>
      <p className="text-muted-foreground">
        No content has been added to this course yet.
      </p>
    </section>
  );
}

/** The title, the totals, and the button that collapses or expands every module. */
function ContentHeader({
  headingId,
  outline,
  collapsed,
  onToggleAll,
}: {
  headingId: string;
  outline: CourseOutline;
  collapsed: CollapsedSections;
  onToggleAll: () => void;
}) {
  return (
    <div className="mb-3 flex items-start justify-between gap-4">
      <div>
        <h2 id={headingId} className="text-2xl font-bold">
          Course Content
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          {outlineSummary(outline)}
        </p>
      </div>
      <Button
        variant="ghost"
        size="sm"
        className="mt-1 text-muted-foreground"
        onClick={onToggleAll}
      >
        {expansionToggleLabel(collapsed)}
      </Button>
    </div>
  );
}

type SectionProps = {
  section: OutlineSection;
  course: CourseRef;
  idPrefix: string;
  isCollapsed: boolean;
  onToggle: () => void;
  mode: LessonMode;
  showProgress: boolean;
};

function Section({
  section,
  course,
  idPrefix,
  isCollapsed,
  onToggle,
  mode,
  showProgress,
}: SectionProps) {
  const listId = `${idPrefix}-lessons-${section.id}`;

  return (
    <div>
      <SectionHeader
        section={section}
        courseSlug={course.slug}
        listId={listId}
        isCollapsed={isCollapsed}
        onToggle={onToggle}
        showProgress={showProgress}
      />
      <ul id={listId} hidden={isCollapsed} className="pb-1.5">
        {section.lessons.map((lesson) => (
          <LessonRow
            key={lesson.id}
            lesson={lesson}
            mode={mode}
            href={lessonHref(mode, course, lesson.id)}
          />
        ))}
      </ul>
    </div>
  );
}

function SectionHeader({
  section,
  courseSlug,
  listId,
  isCollapsed,
  onToggle,
  showProgress,
}: {
  section: OutlineSection;
  courseSlug: string;
  listId: string;
  isCollapsed: boolean;
  onToggle: () => void;
  showProgress: boolean;
}) {
  return (
    <div className="flex items-center gap-2 bg-muted/40 py-1.5 pr-3 pl-1.5">
      <button
        type="button"
        aria-expanded={!isCollapsed}
        aria-controls={listId}
        aria-label={`${isCollapsed ? "Expand" : "Collapse"} ${section.title}`}
        onClick={onToggle}
        className="flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
      >
        <ChevronDown
          className={cn(
            "size-4 transition-transform",
            isCollapsed && "-rotate-90"
          )}
        />
      </button>
      <h3 className="min-w-0 flex-1 truncate text-sm font-semibold">
        <Link
          to={`/courses/${courseSlug}/${section.id}`}
          className="hover:underline"
        >
          {section.title}
        </Link>
      </h3>
      <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
        {sectionSummary(section, { showProgress })}
      </span>
    </div>
  );
}

/** Where a lesson in this mode leads: its editor, its page, or nowhere. */
function lessonHref(mode: LessonMode, course: CourseRef, lessonId: number) {
  switch (mode) {
    case "edit":
      return `/instructor/${course.id}/lessons/${lessonId}`;
    case "view":
      return `/courses/${course.slug}/lessons/${lessonId}`;
    case "locked":
      return null;
  }
}

/** One line: a status or edit icon, the title, and the duration in a column
 * that lines up down the list. */
function LessonRow({
  lesson,
  mode,
  href,
}: {
  lesson: OutlineLesson;
  mode: LessonMode;
  href: string | null;
}) {
  const layout =
    "grid grid-cols-[1rem_minmax(0,1fr)_auto] items-center gap-2.5 py-1.5 pr-3 pl-5 text-sm";
  const content = (
    <>
      <LessonIcon mode={mode} status={lesson.status} />
      <span className="min-w-0">{lesson.title}</span>
      {lesson.durationLabel && (
        <span className="text-right text-xs text-muted-foreground tabular-nums">
          {lesson.durationLabel}
        </span>
      )}
    </>
  );

  return (
    <li>
      {href ? (
        <Link to={href} className={cn(layout, "rounded-md hover:bg-muted")}>
          {content}
        </Link>
      ) : (
        <div className={layout}>{content}</div>
      )}
    </li>
  );
}

function LessonIcon({
  mode,
  status,
}: {
  mode: LessonMode;
  status: LessonProgressStatus;
}) {
  if (mode === "edit") {
    return <Pencil className="size-4 text-muted-foreground" />;
  }
  if (mode === "view" && status === LessonProgressStatus.Completed) {
    return <CheckCircle2 className="size-4 text-green-500" />;
  }
  if (mode === "view" && status === LessonProgressStatus.InProgress) {
    return <PlayCircle className="size-4 text-blue-500" />;
  }
  return <Circle className="size-4 text-muted-foreground" />;
}
