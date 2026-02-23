"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Moon, Sun, X } from "lucide-react";
import { useTheme } from "@/components/providers/theme-provider";

export function Navbar() {
  const pathname = usePathname();
  const isHome = pathname === "/";
  const mobileMenuRef = useRef<HTMLDetailsElement | null>(null);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const { theme, toggleTheme } = useTheme();
  const isCompact = true;
  const [isInHeroZone, setIsInHeroZone] = useState(true);

  const menuItems = [
    { label: "Home", href: "/" },
    { label: "Services", href: isHome ? "#services" : "/#services" },
    { label: "Packages", href: isHome ? "#packages" : "/#packages" },
    { label: "AI Voices", href: "/ai-voices" },
    { label: "Contact", href: isHome ? "#contact" : "/#contact" },
  ];

  const closeMobileMenu = useCallback(() => {
    const details = mobileMenuRef.current;
    if (details?.hasAttribute("open")) details.removeAttribute("open");
    setMobileMenuOpen(false);
  }, []);

  useEffect(() => {
    closeMobileMenu();
  }, [closeMobileMenu, pathname]);

  useEffect(() => {
    if (!mobileMenuOpen) return;

    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      closeMobileMenu();
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = prevOverflow;
    };
  }, [closeMobileMenu, mobileMenuOpen]);

  useEffect(() => {
    if (!isHome) return;

    let rafId = 0;
    const update = () => {
      rafId = 0;
      const heroEndY = window.innerHeight - 1;
      setIsInHeroZone(window.scrollY < heroEndY);
    };

    const onScroll = () => {
      if (rafId) return;
      rafId = window.requestAnimationFrame(update);
    };

    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      if (rafId) window.cancelAnimationFrame(rafId);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [isHome]);

  return (
    <nav
      aria-label="Primary"
      className={[
        "home-navbar-fixed dark px-4 sm:px-6 md:px-8 flex items-center h-[var(--home-navbar-height)]",
        isHome && !isInHeroZone ? "home-navbar-scrolled" : "",
        mobileMenuOpen ? "home-navbar-menu-open" : "",
      ].join(" ")}
      data-theme={theme}
      style={{ fontFamily: "var(--font-manrope)" }}
    >
      <div className="mx-auto w-full max-w-6xl">
        <div className="grid w-full h-full grid-cols-[auto_1fr_auto] items-center px-1 sm:px-2">
          <div className="flex items-center gap-3 justify-self-start">
            <details
              ref={mobileMenuRef}
              className="relative md:hidden group"
              onToggle={(event) => {
                setMobileMenuOpen(event.currentTarget.open);
              }}
            >
              <summary
                className="home-menu-toggle list-none cursor-pointer"
                style={{ color: "rgba(226, 232, 240, 0.95)" }}
                aria-label="Open navigation menu"
                aria-haspopup="menu"
                aria-expanded={mobileMenuOpen}
              >
                {!mobileMenuOpen ? (
                  <svg
                    viewBox="0 0 24 24"
                    width="20"
                    height="20"
                    aria-hidden="true"
                    focusable="false"
                  >
                    <path
                      d="M4 6h16M4 12h16M4 18h16"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                    />
                  </svg>
                ) : null}
              </summary>

              <div
                className="home-mobile-overlay"
                aria-hidden="true"
                onClick={() => {
                  closeMobileMenu();
                }}
              />

              <div className="home-mobile-panel" role="menu" aria-label="Mobile">
                <div className="flex items-center justify-between pb-2">
                  <Link
                    href="/"
                    className={[
                      "home-mobile-link text-sm font-medium tracking-tight focus-visible:outline-none text-foreground/90 hover:text-foreground",
                    ].join(" ")}
                    onClick={() => {
                      closeMobileMenu();
                    }}
                  >
                    Talk-Lee
                  </Link>
                  <button
                    type="button"
                    className="home-menu-toggle"
                    aria-label="Close navigation menu"
                    onClick={() => {
                      closeMobileMenu();
                    }}
                  >
                    <X width={20} height={20} aria-hidden />
                  </button>
                </div>
                <ul className="grid gap-1" role="list">
                  {menuItems.map((item) => (
                    <li key={item.label}>
                      <Link
                        href={item.href}
                        className={[
                          "home-mobile-link text-sm font-medium focus-visible:outline-none text-foreground/90 hover:text-foreground",
                        ].join(" ")}
                        onClick={() => {
                          closeMobileMenu();
                        }}
                      >
                        {item.label}
                      </Link>
                    </li>
                  ))}
                  <li className="mt-1 border-t border-border/60 pt-1">
                    <Link
                      href="/dashboard"
                      className={[
                        "home-mobile-link text-sm font-medium focus-visible:outline-none text-foreground/90 hover:text-foreground",
                      ].join(" ")}
                      onClick={() => {
                        closeMobileMenu();
                      }}
                    >
                      Dashboard
                    </Link>
                  </li>
                  <li>
                    <Link
                      href="/dashboard"
                      className={[
                        "home-mobile-link text-sm font-medium focus-visible:outline-none text-foreground/90 hover:text-foreground",
                      ].join(" ")}
                      onClick={() => {
                        closeMobileMenu();
                      }}
                    >
                      Start Free Trial
                    </Link>
                  </li>
                </ul>
              </div>
            </details>
            <Link
              href="/"
              className={[
                "font-medium tracking-tight focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background rounded-lg transition-[font-size] duration-200 ease-out",
                isCompact ? "text-lg" : "text-xl",
                "text-foreground hover:text-foreground",
              ].join(" ")}
              aria-label="Talk-Lee home"
            >
              Talk-Lee
            </Link>
          </div>

          <ul className="hidden md:flex items-center justify-center gap-4 lg:gap-6" role="list">
            {menuItems.map((item) => (
              <li key={item.label} className="relative">
                <Link
                  href={item.href}
                  className={[
                    "home-nav-link text-sm font-medium focus-visible:outline-none",
                    "text-foreground/80 hover:text-foreground",
                  ].join(" ")}
                  aria-current={item.href === "/" ? "page" : undefined}
                >
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>

          <div className="flex items-center gap-3 justify-self-end">
            <div className="hidden md:inline-flex">
              <Link
                href="/dashboard"
                className={[
                  "home-nav-link text-sm font-medium focus-visible:outline-none",
                  "text-foreground/80 hover:text-foreground",
                ].join(" ")}
              >
                Dashboard
              </Link>
            </div>
            <Link
              href="/dashboard"
              className={[
                "hidden md:inline-flex px-4 text-sm font-medium rounded-xl bg-indigo-600 text-white hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-400 transition-[background-color,box-shadow] duration-200 ease-out shadow-md hover:shadow-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
                isCompact ? "py-1.5" : "py-2",
              ].join(" ")}
            >
              Start Free Trial
            </Link>
            <button
              type="button"
              onClick={toggleTheme}
              className={[
                "inline-flex items-center justify-center rounded-xl hover:scale-[1.03] transition-[background-color,transform,color,width,height] duration-[250ms] ease-in-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
                "text-foreground/80 hover:text-foreground hover:bg-white/10",
                isCompact ? "w-9 h-9" : "w-10 h-10",
              ].join(" ")}
              aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              aria-pressed={theme === "dark"}
              title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            >
              {theme === "dark" ? (
                <Sun width={18} height={18} aria-hidden />
              ) : (
                <Moon width={18} height={18} aria-hidden />
              )}
            </button>
          </div>
        </div>
      </div>
    </nav>
  );
}
