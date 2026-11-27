import { useEffect, useState, type ReactNode } from "react";
import { Link, useFetcher } from "react-router";
import { toast } from "sonner";
import {
  BookOpen,
  Clock,
  Pencil,
  PlayCircle,
  ShieldAlert,
  Users,
} from "lucide-react";
import { Button } from "~/components/ui/button";
import { Card, CardContent, CardHeader } from "~/components/ui/card";
import { Textarea } from "~/components/ui/textarea";
import { UserAvatar } from "~/components/user-avatar";
import { StarRatingInput } from "~/components/star-rating";
import { formatDuration, formatPrice } from "~/lib/utils";
import { countryName } from "~/lib/ppp";
import type { RefundOffer } from "~/lib/refunds.server";

type CourseFacts = {
  id: number;
  slug: string;
  instructorName: string;
  instructorAvatarUrl: string | null;
  instructorBio: string | null;
};

type CountryLock = { purchaseCountry: string; currentCountry: string };

/** What an enrolled Student's card shows. Null for anyone not enrolled. */
export type EnrollmentView = {
  canTake: boolean;
  countryLock: CountryLock | null;
  progress: number;
  nextLessonId: number | null;
  teamPurchaseLink: string;
  userRating: number | null;
  refundOffer: RefundOffer | null;
};

type CourseSidebarProps = {
  course: CourseFacts & { firstLessonId: number | null };
  lessonCount: number;
  totalDuration: number;
  isInstructor: boolean;
  enrollment: EnrollmentView | null;
  /** Shown to someone who is neither the Instructor nor enrolled. */
  enrollButton: ReactNode;
};

/** The right-hand card: what this Person can do with the course, then the
 * facts about it. */
export function CourseSidebar({
  course,
  lessonCount,
  totalDuration,
  isInstructor,
  enrollment,
  enrollButton,
}: CourseSidebarProps) {
  const heading = isInstructor
    ? "Your Course"
    : enrollment
      ? "Your Progress"
      : "Get Started";

  return (
    <Card className="sticky top-6">
      <CardHeader>
        <h2 className="text-lg font-semibold">{heading}</h2>
      </CardHeader>
      <CardContent className="space-y-4">
        {isInstructor ? (
          <Link to={`/instructor/${course.id}`}>
            <Button className="w-full">
              <Pencil className="mr-2 size-4" />
              Manage Course
            </Button>
          </Link>
        ) : enrollment ? (
          <EnrollmentPanel course={course} enrollment={enrollment} />
        ) : (
          enrollButton
        )}
        <CourseFacts
          course={course}
          lessonCount={lessonCount}
          totalDuration={totalDuration}
        />
      </CardContent>
    </Card>
  );
}

function CourseFacts({
  course,
  lessonCount,
  totalDuration,
}: {
  course: CourseFacts;
  lessonCount: number;
  totalDuration: number;
}) {
  return (
    <div className="space-y-2 pt-2 text-sm text-muted-foreground">
      <div className="flex items-center gap-2">
        <BookOpen className="size-4" />
        <span>{lessonCount} lessons</span>
      </div>
      <div className="flex items-center gap-2">
        <Clock className="size-4" />
        <span>{formatDuration(totalDuration, true, false, false)} total</span>
      </div>
      <div className="flex items-center gap-2">
        <UserAvatar
          name={course.instructorName}
          avatarUrl={course.instructorAvatarUrl}
          className="size-5"
        />
        <span>Taught by {course.instructorName}</span>
      </div>
      {course.instructorBio && (
        <p className="mt-2 text-xs leading-relaxed">{course.instructorBio}</p>
      )}
    </div>
  );
}

/** What an enrolled Student sees: a country lock notice, their progress and
 * the next lesson, more seats, a rating and, if offered, a refund. */
function EnrollmentPanel({
  course,
  enrollment,
}: {
  course: Pick<CourseFacts, "slug"> & { firstLessonId: number | null };
  enrollment: EnrollmentView;
}) {
  return (
    <>
      {enrollment.countryLock && (
        <CountryLockNotice countryLock={enrollment.countryLock} />
      )}
      {enrollment.canTake && (
        <ProgressPanel
          course={course}
          progress={enrollment.progress}
          nextLessonId={enrollment.nextLessonId}
        />
      )}
      <Link to={enrollment.teamPurchaseLink}>
        <Button variant="outline" className="w-full">
          <Users className="mr-2 size-4" />
          Buy More Seats
        </Button>
      </Link>
      {enrollment.canTake && <RateCourse userRating={enrollment.userRating} />}
      {enrollment.refundOffer && (
        <RequestRefund
          slug={course.slug}
          purchaseId={enrollment.refundOffer.purchaseId}
          amount={enrollment.refundOffer.amount}
        />
      )}
    </>
  );
}

function CountryLockNotice({ countryLock }: { countryLock: CountryLock }) {
  return (
    <div className="flex gap-2 rounded-md border border-amber-500/50 bg-amber-500/10 p-3 text-sm">
      <ShieldAlert className="mt-0.5 size-4 shrink-0 text-amber-500" />
      <p>
        You bought this course at a Purchasing-Power Price in{" "}
        <strong>{countryName(countryLock.purchaseCountry)}</strong>, so it
        can&apos;t be used from{" "}
        <strong>{countryName(countryLock.currentCountry)}</strong>.
      </p>
    </div>
  );
}

function ProgressPanel({
  course,
  progress,
  nextLessonId,
}: {
  course: Pick<CourseFacts, "slug"> & { firstLessonId: number | null };
  progress: number;
  nextLessonId: number | null;
}) {
  const targetLessonId = nextLessonId ?? course.firstLessonId;

  return (
    <>
      <div className="mb-2 flex items-center justify-between text-sm">
        <span>{progress}% complete</span>
      </div>
      <div className="mb-4 h-2 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-primary transition-all"
          style={{ width: `${progress}%` }}
        />
      </div>
      {targetLessonId && (
        <Link to={`/courses/${course.slug}/lessons/${targetLessonId}`}>
          <Button className="w-full">
            <PlayCircle className="mr-2 size-4" />
            {progress > 0 ? "Continue Learning" : "Start Course"}
          </Button>
        </Link>
      )}
    </>
  );
}

function RateCourse({ userRating }: { userRating: number | null }) {
  const fetcher = useFetcher<{ ok: boolean; error: string | null }>();
  const isSubmitting = fetcher.state !== "idle";

  // Show the star the user just clicked while the request is in flight.
  const pendingRating = fetcher.formData?.get("rating");
  const displayedRating =
    pendingRating !== null && pendingRating !== undefined
      ? Number(pendingRating)
      : userRating;

  useEffect(() => {
    if (fetcher.state === "idle" && fetcher.data?.error) {
      toast.error(fetcher.data.error);
    }
  }, [fetcher.state, fetcher.data]);

  return (
    <div className="border-t pt-4">
      <p className="mb-2 text-sm font-medium">
        {userRating ? "Your rating" : "Rate this course"}
      </p>
      <fetcher.Form method="post">
        <StarRatingInput value={displayedRating} disabled={isSubmitting} />
      </fetcher.Form>
      {userRating && (
        <p className="mt-1.5 text-xs text-muted-foreground">
          Click a star to change your rating.
        </p>
      )}
    </div>
  );
}

function RequestRefund({
  slug,
  purchaseId,
  amount,
}: {
  slug: string;
  purchaseId: number;
  amount: number;
}) {
  const fetcher = useFetcher<{ errors?: Record<string, string> }>();
  const [isFormOpen, setIsFormOpen] = useState(false);
  const isSubmitting = fetcher.state !== "idle";

  useEffect(() => {
    const firstError = Object.values(fetcher.data?.errors ?? {})[0];
    if (fetcher.state === "idle" && firstError) {
      toast.error(firstError);
    }
  }, [fetcher.state, fetcher.data]);

  if (!isFormOpen) {
    return (
      <div className="border-t pt-4">
        <Button
          variant="ghost"
          size="sm"
          className="w-full text-muted-foreground"
          onClick={() => setIsFormOpen(true)}
        >
          Request a refund
        </Button>
      </div>
    );
  }

  return (
    <RefundForm
      slug={slug}
      purchaseId={purchaseId}
      amount={amount}
      isSubmitting={isSubmitting}
      onCancel={() => setIsFormOpen(false)}
      fetcher={fetcher}
    />
  );
}

function RefundForm({
  slug,
  purchaseId,
  amount,
  isSubmitting,
  onCancel,
  fetcher,
}: {
  slug: string;
  purchaseId: number;
  amount: number;
  isSubmitting: boolean;
  onCancel: () => void;
  fetcher: ReturnType<typeof useFetcher<{ errors?: Record<string, string> }>>;
}) {
  return (
    <div className="border-t pt-4">
      <p className="mb-2 text-sm font-medium">Request a refund</p>
      <p className="mb-3 text-xs text-muted-foreground">
        We will send {formatPrice(amount)} back to the card you paid with, once
        someone has read your note.
      </p>
      <fetcher.Form
        method="post"
        action={`/courses/${slug}/refund`}
        className="space-y-2"
      >
        <input type="hidden" name="purchaseId" value={purchaseId} />
        <Textarea
          name="reason"
          rows={3}
          required
          placeholder="What went wrong?"
        />
        <div className="flex gap-2">
          <Button type="submit" size="sm" disabled={isSubmitting}>
            {isSubmitting ? "Sending..." : "Request a refund"}
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
            Keep it
          </Button>
        </div>
      </fetcher.Form>
    </div>
  );
}
