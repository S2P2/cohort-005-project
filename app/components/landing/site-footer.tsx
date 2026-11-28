import { Link } from "react-router";

const footerLink = "transition-colors hover:text-foreground";

export function SiteFooter({ signedIn }: { signedIn: boolean }) {
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
          <Link to="/courses" className={footerLink}>
            Courses
          </Link>
          {signedIn ? (
            <Link to="/dashboard" className={footerLink}>
              Dashboard
            </Link>
          ) : (
            <>
              <Link to="/login" className={footerLink}>
                Log In
              </Link>
              <Link to="/signup" className={footerLink}>
                Sign Up
              </Link>
            </>
          )}
        </nav>
      </div>
    </footer>
  );
}
