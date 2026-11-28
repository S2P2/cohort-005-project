import { BookOpen, GraduationCap, ListChecks } from "lucide-react";
import type { LandingStats } from "~/modules/landing-page/index.server";

function Stat({
  icon: Icon,
  value,
  label,
}: {
  icon: typeof BookOpen;
  value: number;
  label: string;
}) {
  return (
    <div className="flex items-center gap-4">
      <span className="flex size-12 shrink-0 items-center justify-center rounded-lg bg-primary/10">
        <Icon className="size-6 text-primary" />
      </span>
      <div>
        <p className="text-3xl font-bold tracking-tight">{value}</p>
        <p className="text-sm text-muted-foreground">
          {value === 1 ? label : `${label}s`}
        </p>
      </div>
    </div>
  );
}

export function StatsBand({ stats }: { stats: LandingStats }) {
  return (
    <section className="border-y border-border bg-muted/50">
      <div className="mx-auto grid max-w-6xl grid-cols-1 gap-8 px-4 py-14 sm:grid-cols-3 sm:px-6 sm:py-16">
        <Stat icon={BookOpen} value={stats.courseCount} label="Course" />
        <Stat
          icon={GraduationCap}
          value={stats.instructorCount}
          label="Instructor"
        />
        <Stat icon={ListChecks} value={stats.lessonCount} label="Lesson" />
      </div>
    </section>
  );
}
