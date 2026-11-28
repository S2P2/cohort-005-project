import { useEffect, useState } from "react";
import { Link } from "react-router";
import { Moon, Sun } from "lucide-react";
import { Button } from "~/components/ui/button";

type Person = { id: number; name: string; role: string };

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

export function SiteHeader({ currentUser }: { currentUser: Person | null }) {
  return (
    <header className="border-b border-border">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
        <Link to="/" className="text-xl font-bold tracking-tight">
          Cadence
        </Link>
        <nav className="flex items-center gap-3 sm:gap-6">
          <Link to="/courses" className={navLink}>
            Courses
          </Link>
          <ThemeToggle />
          {currentUser ? (
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
