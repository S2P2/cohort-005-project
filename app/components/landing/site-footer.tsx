import { Link } from "react-router";

export function SiteFooter() {
  return (
    <footer className="border-t border-border">
      <div className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-8 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <div>
          <p className="font-semibold">Cadence</p>
          <p className="text-sm text-muted-foreground">
            Learn to code, at your own pace.
          </p>
        </div>
        <nav className="flex gap-6 text-sm text-muted-foreground">
          <Link
            to="/courses"
            className="transition-colors hover:text-foreground"
          >
            Courses
          </Link>
          <Link to="/login" className="transition-colors hover:text-foreground">
            Log In
          </Link>
          <Link
            to="/signup"
            className="transition-colors hover:text-foreground"
          >
            Sign Up
          </Link>
        </nav>
      </div>
    </footer>
  );
}
