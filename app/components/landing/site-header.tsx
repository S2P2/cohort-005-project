import { useEffect, useState } from "react";
import { Link } from "react-router";
import { Moon, Sun } from "lucide-react";
import { Button } from "~/components/ui/button";

const navLink =
  "whitespace-nowrap text-sm font-medium text-muted-foreground transition-colors hover:text-foreground";

function ThemeToggle() {
  const [isDark, setIsDark] = useState(false);

  useEffect(() => {
    setIsDark(document.documentElement.classList.contains("dark"));
  }, []);

  function toggle() {
    const next = !isDark;
    setIsDark(next);
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("cadence-theme", next ? "dark" : "light");
    } catch {}
  }

  return (
    <button
      onClick={toggle}
      className="rounded-md p-2 text-muted-foreground transition-colors hover:text-foreground"
      title={isDark ? "Switch to light mode" : "Switch to dark mode"}
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
    >
      {isDark ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </button>
  );
}

export function SiteHeader({ signedIn }: { signedIn: boolean }) {
  return (
    <header className="border-b border-border">
      <div className="mx-auto flex min-h-16 max-w-6xl items-center justify-between gap-4 px-4 py-2 sm:px-6">
        <Link to="/" className="text-xl font-bold tracking-tight">
          Cadence
        </Link>
        {/* Under 360px the nav cannot fit beside the brand, so it wraps
            inside itself rather than running the Sign Up button off the
            edge. From 360px it stays on one row. */}
        <nav className="flex flex-wrap items-center justify-end gap-x-3 gap-y-1 min-[360px]:flex-nowrap sm:gap-6">
          <Link to="/courses" className={navLink}>
            Courses
          </Link>
          <ThemeToggle />
          {signedIn ? (
            <Button asChild size="sm">
              <Link to="/dashboard">Dashboard</Link>
            </Button>
          ) : (
            <>
              <Link to="/login" className={navLink}>
                Log In
              </Link>
              <Button asChild size="sm">
                <Link to="/signup">Sign Up</Link>
              </Button>
            </>
          )}
        </nav>
      </div>
    </header>
  );
}
