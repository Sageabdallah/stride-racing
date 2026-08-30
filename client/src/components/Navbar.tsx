import { useState, useEffect } from "react";
import { Link, useLocation } from "wouter";
import { Menu, X, MessageSquare } from "lucide-react";

const navLinks = [
  { label: "Blackbook", href: "/blackbook", matches: ["/bets", "/blackbook"] },
  { label: "The Track Board", href: "/best-bets", matches: ["/best-bets"] },
  { label: "Race Day", href: "/race-day", matches: ["/race-day"] },
  { label: "Proof", href: "/proof", matches: ["/proof", "/calibration", "/backtest", "/gap-analysis", "/simulations"] },
  { label: "Ask Stride", href: "/ask-stride", matches: ["/ask-stride", "/chat"] },
];

export function Navbar() {
  const [location] = useLocation();
  const [scrolled, setScrolled] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  const isActiveLink = (matches: string[]) => matches.includes(location);

  useEffect(() => {
    const handleScroll = () => setScrolled(window.scrollY > 40);
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  return (
    <nav
      className={`nav-liquid ${scrolled ? "scrolled" : ""}`}
      data-testid="navbar"
    >
      <div className="max-w-[1400px] mx-auto flex items-center justify-between h-16 relative z-[2]">
        <Link href="/">
          <span
            className="font-syne text-[22px] font-extrabold cursor-pointer brand-liquid"
            data-testid="link-brand"
            data-text="WizBet"
          >
            WizBet
          </span>
        </Link>

        <ul className="hidden md:flex items-center gap-8 list-none" data-testid="nav-links">
          {navLinks.map((link) => (
            <li key={link.href}>
              <Link href={link.href}>
                <span
                  className={`nav-link-liquid ${isActiveLink(link.matches) ? "active" : ""}`}
                  data-testid={`link-${link.label.toLowerCase().replace(/\s/g, "-")}`}
                >
                  {link.label}
                </span>
              </Link>
            </li>
          ))}
        </ul>

        <div className="hidden md:block">
          <Link href="/ask-stride">
            <span className="nav-cta-liquid" data-testid="button-nav-cta">
              <MessageSquare className="inline w-4 h-4 mr-1.5 -mt-0.5" />
              Ask Stride
            </span>
          </Link>
        </div>

        <button
          className="md:hidden text-white p-2"
          onClick={() => setMobileOpen(!mobileOpen)}
          data-testid="button-mobile-menu"
        >
          {mobileOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
        </button>
      </div>

      {mobileOpen && (
        <div className="md:hidden pb-4 relative z-[2]">
          {navLinks.map((link) => (
            <Link key={link.href} href={link.href}>
              <span
                onClick={() => setMobileOpen(false)}
                className={`block px-6 py-3 text-sm font-medium transition-colors ${
                  isActiveLink(link.matches)
                    ? "text-white"
                    : "text-white/50 hover:text-white"
                }`}
                data-testid={`link-mobile-${link.label.toLowerCase().replace(/\s/g, "-")}`}
              >
                {link.label}
              </span>
            </Link>
          ))}
          <div className="px-6 pt-2">
            <Link href="/ask-stride">
              <span className="nav-cta-liquid inline-block" data-testid="button-mobile-cta">
                Ask Stride
              </span>
            </Link>
          </div>
        </div>
      )}
    </nav>
  );
}
