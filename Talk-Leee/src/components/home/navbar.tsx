"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bot, ChevronDown, PhoneCall, Sparkles, Moon, Sun, X, Headphones, BadgeCheck } from "lucide-react";
import { useTheme } from "@/components/providers/theme-provider";
import { getBrowserAuthToken, setBrowserAuthToken } from "@/lib/auth-token";
import { industryNavItems } from "@/Industries/industries";

export function Navbar() {
  const pathname = usePathname();
  const router = useRouter();
  const isHome = pathname === "/";
  const isAiVoices = pathname === "/ai-voices" || pathname.startsWith("/ai-voices/");
  const isUseCasesPage = pathname.startsWith("/use-cases");
  const isIndustriesPage = pathname.startsWith("/industries");
  const isProductsPage =
    pathname === "/ai-voice-dialer" ||
    pathname.startsWith("/ai-voice-dialer/") ||
    pathname === "/ai-assist" ||
    pathname.startsWith("/ai-assist/") ||
    pathname === "/ai-voice-agent" ||
    pathname.startsWith("/ai-voice-agent/");
  const mobileMenuRef = useRef<HTMLDetailsElement | null>(null);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const { theme, toggleTheme } = useTheme();
  const isCompact = true;
  const [isInHeroZone, setIsInHeroZone] = useState(true);
  const [suppressedDropdownLabel, setSuppressedDropdownLabel] = useState<string | null>(null);
  const prefetchedRef = useRef<Set<string>>(new Set());

  const menuItems = useMemo(() => {
    return [
      { label: "Home", href: "/" },
      {
        label: "Products",
        items: [
          {
            label: "AI Voice Dialer",
            href: "/ai-voice-dialer",
            description: "Automate calls, emails, and workflows with human-like voice agents.",
            icon: PhoneCall,
          },
          {
            label: "AI Assist",
            href: "/ai-assist",
            description: "Real-time guidance, call insights, and automated follow-ups for teams.",
            icon: Sparkles,
          },
          {
            label: "AI Voice Agent",
            href: "/ai-voice-agent",
            description: "Smarter conversations with natural dialogue and seamless handoffs.",
            icon: Bot,
          },
        ],
      },
      {
        label: "Use Cases",
        items: [
          {
            label: "Customer Services & Support",
            href: "/use-cases/customer-services-support",
            description: "Deliver faster resolutions and consistent support with AI-powered conversations.",
            icon: Headphones,
          },
          {
            label: "Automated Lead Qualification",
            href: "/use-cases/automated-lead-qualification",
            description: "Engage, score, and route leads instantly so reps focus on high-intent prospects.",
            icon: BadgeCheck,
          },
        ],
      },
      { label: "Industries", items: [...industryNavItems] },
      { label: "FAQ", href: isHome ? "#faq" : "/#faq" },
      { label: "Contact", href: isHome ? "#contact" : "/#contact" },
    ];
  }, [isHome]);

  type MenuItem = (typeof menuItems)[number];
  type DropdownWithChildrenItem = Extract<MenuItem, { items: unknown[] }>;
  type LinkItem = Extract<MenuItem, { href: string }>;

  const isDropdownWithChildrenItem = (item: MenuItem): item is DropdownWithChildrenItem =>
    "items" in item && Array.isArray(item.items) && item.items.length > 0;

  const isLinkItem = (item: MenuItem): item is LinkItem => "href" in item && typeof item.href === "string";

  const prefetchHref = useCallback(
    (href: string) => {
      if (!href.startsWith("/")) return;
      if (prefetchedRef.current.has(href)) return;
      prefetchedRef.current.add(href);
      try {
        router.prefetch(href);
      } catch {}
    },
    [router]
  );

  useEffect(() => {
    if (process.env.NODE_ENV !== "development") return;
    const token = getBrowserAuthToken();
    if (token) return;
    setBrowserAuthToken("dev-token");
  }, []);

  const closeMobileMenu = useCallback(() => {
    const details = mobileMenuRef.current;
    if (details?.hasAttribute("open")) details.removeAttribute("open");
    setMobileMenuOpen(false);
  }, []);

  const scrollToHash = useCallback((hash: string) => {
    const id = hash.startsWith("#") ? hash.slice(1) : "";
    if (!id) return;

    const attempt = (remaining: number) => {
      const el = document.getElementById(id);
      if (el) {
        if (window.location.hash !== hash) window.history.pushState(null, "", hash);
        el.scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
      if (remaining <= 0) return;
      window.setTimeout(() => attempt(remaining - 1), 120);
    };

    attempt(60);
  }, []);

  useEffect(() => {
    closeMobileMenu();
  }, [closeMobileMenu, pathname]);

  useEffect(() => {
    setSuppressedDropdownLabel(null);
  }, [pathname]);

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
    <>
      <nav
        aria-label="Primary"
        className={[
          "home-navbar-fixed dark px-3 sm:px-4 md:px-5 flex items-center h-[var(--home-navbar-height)]",
          isAiVoices || isUseCasesPage || isIndustriesPage || isProductsPage || (isHome && !isInHeroZone) ? "home-navbar-scrolled" : "",
          isAiVoices || isUseCasesPage || isIndustriesPage || isProductsPage ? "home-navbar-page" : "",
          mobileMenuOpen ? "home-navbar-menu-open" : "",
        ].join(" ")}
        data-theme={theme}
        style={{
          fontFamily: "var(--font-manrope)",
          ...(isAiVoices
            ? {
                background: "rgba(0, 16, 34, 0.78)",
              }
            : null),
        }}
      >
        <div className="w-full">
          <div className="navbarInnerGrid grid h-full grid-cols-[1fr_auto] md:grid-cols-[auto_auto_auto] items-center gap-3 md:gap-7">
            <div className="flex items-center gap-3 justify-self-start">
              <details
                ref={mobileMenuRef}
                className="navbarMobileMenu relative md:hidden group"
                onToggle={(event) => {
                  setMobileMenuOpen(event.currentTarget.open);
                }}
              >
              <summary
                className="home-menu-toggle list-none cursor-pointer"
                style={{
                  color: isAiVoices ? "#7dd3fc" : "rgba(226, 232, 240, 0.95)",
                }}
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
                      "home-mobile-link flex items-center gap-2 text-sm font-medium tracking-tight focus-visible:outline-none text-foreground/90 hover:text-foreground",
                    ].join(" ")}
                    onClick={() => {
                      closeMobileMenu();
                    }}
                  >
                    <svg viewBox="327 327 369 369" className="w-6 h-6" aria-hidden="true" fill="currentColor">
                      <path d="m547.23 349.3q2.35 0.84 4.77 1.7c2.95 0.95 5.9 1.9 8.94 2.87 22.37 8.68 39.25 22.32 56.06 39.13q3.62 4.92 7 10 1.69 2.52 3.44 5.12c11.29 18.62 16.5 35.46 19.56 56.88q0.06 9.5 0 19 2.46 0.98 5 2c9.18 6.91 16.17 13.96 21.81 24 4.58 10.47 6.08 19.98 6.5 31.44-0.38 11.7-2.05 22.04-7.31 32.56-6.13 10.3-12.57 18.37-22.13 25.69-7.89 4.95-16 9.35-24.87 12.31-1.49 3.46-1.49 3.46-3 7-4.27 8.45-8.73 15.88-15 23q-0.99 0-2 0 0 0.98 0 2c-10.16 9.19-20.99 15.16-33.81 19.87-9.76 2.89-18 4.39-28.19 4.13-0.66 2.31-1.32 4.62-2 7-1.98 1.65-3.96 3.3-6 5-6.75 0.47-13.19 0.65-19.94 0.56q-2.72 0.02-5.52 0.04c-4.85-0.03-9.7-0.3-14.54-0.6-7-4-7-4-13-11-2-5-2-5-2.44-12.25 0.44-7.75 0.44-7.75 2.5-12.88 2.94-3.87 2.94-3.87 9.94-8.87 6.75-2.25 14.65-1.27 21.75-1.31q2.6-0.05 5.27-0.09c4.66-0.02 9.32 0.18 13.98 0.4 7 4 7 4 10 8q0 0.98 0 2c10.53-0.96 20.22-1.87 30-6 10-6 17.3-12.21 24.06-21.69 1.46-2.63 1.46-2.63 2.94-5.31-0.65-4.88-0.65-4.88-3-8-0.21-3.81-0.28-7.62-0.29-11.43-0.01-2.42-0.03-4.84-0.04-7.33 0-2.63 0-5.26-0.01-7.97-0.01-4.03-0.01-4.03-0.02-8.15q-0.01-8.54-0.01-17.09-0.01-13.1-0.08-26.19-0.01-8.29-0.01-16.57c-0.02-2.63-0.03-5.25-0.04-7.95 0-2.44 0.01-4.87 0.01-7.37 0-2.15 0-4.29 0-6.5 0.49-5.45 0.49-5.45 4.49-12.45q2.46-1.48 5-3 8-0.19 16 0c0-14.03-3.54-26.14-9-39-7.15-14.3-16.6-26.87-29-37-16.61-12.49-31.88-19.49-52-24q-2.92-0.68-5.94-1.38c-16.98-1.75-33.39-1.39-49.74 3.89-17.42 6.12-31.41 14.71-45.45 26.74-11.15 10.92-20.16 23.22-25.85 37.84-4.02 11.34-7.21 21.84-8.02 33.91q0.99-0.49 2-1c6.62-0.31 6.62-0.31 14 0 2.64 1.32 5.28 2.64 8 4q1.48 2.46 3 5c0.29 3.87 0.4 7.76 0.42 11.64 0.02 3.62 0.02 3.62 0.05 7.32 0 2.62 0 5.24 0 7.93 0.01 4.03 0.01 4.03 0.02 8.13q0.02 8.52 0.01 17.04 0.01 13.06 0.09 26.11 0 8.27 0 16.54c0.02 2.61 0.04 5.22 0.05 7.91-0.01 2.43-0.02 4.85-0.02 7.35 0 2.14 0 4.27 0 6.47-0.31 2.75-0.31 2.75-0.62 5.56-1.65 2.64-3.3 5.28-5 8-4 2-4 2-11.56 2.31-9.78-0.36-16.57-2.03-25.44-6-10.84-5.98-19.14-12.72-27-22.31-4.93-7.84-8.78-15.01-11-24-0.66-2.31-1.32-4.62-2-7q-0.23-5.18-0.19-10.38 0.01-2.65 0.02-5.39c0.36-11 2.68-19.24 7.17-29.23 3.31-4.81 3.31-4.81 7-9 1.49-1.73 2.97-3.47 4.5-5.25 4.5-4.75 4.5-4.75 11.5-9.75q0.99 0 2 0c-0.08-3.26-0.16-6.52-0.25-9.88 0.02-16.7 4.12-31.64 10.25-47.12 9.91-20.77 22.78-35.78 39-52 17.04-13.37 36.11-22.55 57-28 21.94-3.48 44.89-4.61 66.23 2.3z"/>
                    </svg>
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
                  {menuItems.map((item) => {
                    return (
                      <li key={item.label}>
                        {isDropdownWithChildrenItem(item) ? (
                          <details className="group">
                            <summary
                              className={[
                                "home-mobile-link text-sm font-medium focus-visible:outline-none text-foreground/90 hover:text-foreground cursor-pointer list-none",
                              ].join(" ")}
                            >
                              {item.label}
                            </summary>
                            <div className="ml-3 mt-1 grid gap-1">
                              {item.items.map((child) => (
                                <Link
                                  key={child.href}
                                  href={child.href}
                                  className={[
                                    "home-mobile-link text-sm font-medium focus-visible:outline-none text-foreground/90 hover:text-foreground",
                                  ].join(" ")}
                                  onClick={() => {
                                    closeMobileMenu();
                                  }}
                                >
                                  {child.label}
                                </Link>
                              ))}
                            </div>
                          </details>
                        ) : isLinkItem(item) ? (
                          <Link
                            href={item.href}
                            className={[
                              "home-mobile-link text-sm font-medium focus-visible:outline-none text-foreground/90 hover:text-foreground",
                            ].join(" ")}
                            onClick={() => {
                              if (isHome && item.href.startsWith("#")) {
                                closeMobileMenu();
                                window.setTimeout(() => scrollToHash(item.href), 0);
                                return;
                              }
                              closeMobileMenu();
                            }}
                          >
                            {item.label}
                          </Link>
                        ) : null}
                      </li>
                    );
                  })}
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
                      Login
                    </Link>
                  </li>
                </ul>
              </div>
            </details>
            <Link
              href="/"
              className={[
                "flex items-center gap-2 font-medium tracking-tight focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background rounded-lg transition-[font-size] duration-200 ease-out",
                isCompact ? "text-base" : "text-lg",
                "text-foreground hover:text-foreground",
              ].join(" ")}
              aria-label="Talk-Lee home"
            >
              <svg viewBox="327 327 369 369" className="w-7 h-7" aria-hidden="true" fill="currentColor">
                <path d="m547.23 349.3q2.35 0.84 4.77 1.7c2.95 0.95 5.9 1.9 8.94 2.87 22.37 8.68 39.25 22.32 56.06 39.13q3.62 4.92 7 10 1.69 2.52 3.44 5.12c11.29 18.62 16.5 35.46 19.56 56.88q0.06 9.5 0 19 2.46 0.98 5 2c9.18 6.91 16.17 13.96 21.81 24 4.58 10.47 6.08 19.98 6.5 31.44-0.38 11.7-2.05 22.04-7.31 32.56-6.13 10.3-12.57 18.37-22.13 25.69-7.89 4.95-16 9.35-24.87 12.31-1.49 3.46-1.49 3.46-3 7-4.27 8.45-8.73 15.88-15 23q-0.99 0-2 0 0 0.98 0 2c-10.16 9.19-20.99 15.16-33.81 19.87-9.76 2.89-18 4.39-28.19 4.13-0.66 2.31-1.32 4.62-2 7-1.98 1.65-3.96 3.3-6 5-6.75 0.47-13.19 0.65-19.94 0.56q-2.72 0.02-5.52 0.04c-4.85-0.03-9.7-0.3-14.54-0.6-7-4-7-4-13-11-2-5-2-5-2.44-12.25 0.44-7.75 0.44-7.75 2.5-12.88 2.94-3.87 2.94-3.87 9.94-8.87 6.75-2.25 14.65-1.27 21.75-1.31q2.6-0.05 5.27-0.09c4.66-0.02 9.32 0.18 13.98 0.4 7 4 7 4 10 8q0 0.98 0 2c10.53-0.96 20.22-1.87 30-6 10-6 17.3-12.21 24.06-21.69 1.46-2.63 1.46-2.63 2.94-5.31-0.65-4.88-0.65-4.88-3-8-0.21-3.81-0.28-7.62-0.29-11.43-0.01-2.42-0.03-4.84-0.04-7.33 0-2.63 0-5.26-0.01-7.97-0.01-4.03-0.01-4.03-0.02-8.15q-0.01-8.54-0.01-17.09-0.01-13.1-0.08-26.19-0.01-8.29-0.01-16.57c-0.02-2.63-0.03-5.25-0.04-7.95 0-2.44 0.01-4.87 0.01-7.37 0-2.15 0-4.29 0-6.5 0.49-5.45 0.49-5.45 4.49-12.45q2.46-1.48 5-3 8-0.19 16 0c0-14.03-3.54-26.14-9-39-7.15-14.3-16.6-26.87-29-37-16.61-12.49-31.88-19.49-52-24q-2.92-0.68-5.94-1.38c-16.98-1.75-33.39-1.39-49.74 3.89-17.42 6.12-31.41 14.71-45.45 26.74-11.15 10.92-20.16 23.22-25.85 37.84-4.02 11.34-7.21 21.84-8.02 33.91q0.99-0.49 2-1c6.62-0.31 6.62-0.31 14 0 2.64 1.32 5.28 2.64 8 4q1.48 2.46 3 5c0.29 3.87 0.4 7.76 0.42 11.64 0.02 3.62 0.02 3.62 0.05 7.32 0 2.62 0 5.24 0 7.93 0.01 4.03 0.01 4.03 0.02 8.13q0.02 8.52 0.01 17.04 0.01 13.06 0.09 26.11 0 8.27 0 16.54c0.02 2.61 0.04 5.22 0.05 7.91-0.01 2.43-0.02 4.85-0.02 7.35 0 2.14 0 4.27 0 6.47-0.31 2.75-0.31 2.75-0.62 5.56-1.65 2.64-3.3 5.28-5 8-4 2-4 2-11.56 2.31-9.78-0.36-16.57-2.03-25.44-6-10.84-5.98-19.14-12.72-27-22.31-4.93-7.84-8.78-15.01-11-24-0.66-2.31-1.32-4.62-2-7q-0.23-5.18-0.19-10.38 0.01-2.65 0.02-5.39c0.36-11 2.68-19.24 7.17-29.23 3.31-4.81 3.31-4.81 7-9 1.49-1.73 2.97-3.47 4.5-5.25 4.5-4.75 4.5-4.75 11.5-9.75q0.99 0 2 0c-0.08-3.26-0.16-6.52-0.25-9.88 0.02-16.7 4.12-31.64 10.25-47.12 9.91-20.77 22.78-35.78 39-52 17.04-13.37 36.11-22.55 57-28 21.94-3.48 44.89-4.61 66.23 2.3z"/>
              </svg>
              Talk-Lee
            </Link>
          </div>

          <ul
            className="navbarDesktopNav hidden md:flex items-center justify-center gap-1.5 lg:gap-2.5"
            role="list"
          >
            {menuItems.map((item) => {
              const isIndustriesDropdown = item.label === "Industries";
              const dropdownWidthClass = isIndustriesDropdown ? "w-[680px]" : item.label === "Products" || item.label === "Use Cases" ? "w-[345px]" : "w-[520px]";
              const dropdownGridClass = isIndustriesDropdown ? "grid-cols-2" : "grid-cols-1";
              return (
                <li key={item.label} className="relative">
                  {isDropdownWithChildrenItem(item) ? (
                    <div
                      className="group relative"
                      onMouseLeave={() => {
                        setSuppressedDropdownLabel(null);
                      }}
                      onMouseEnter={() => {
                        for (const child of item.items) prefetchHref(child.href);
                      }}
                    >
                      <button
                        type="button"
                        className={[
                          "home-nav-link text-[13px] font-medium focus-visible:outline-none",
                          "text-foreground/80 hover:text-foreground",
                          "inline-flex items-center gap-1",
                        ].join(" ")}
                        aria-haspopup="menu"
                      >
                        {item.label}
                        <ChevronDown
                          className="h-4 w-4 transition-transform duration-200 ease-out group-hover:rotate-180 group-focus-within:rotate-180"
                          aria-hidden
                        />
                      </button>
                      <div
                        className={[
                          `absolute left-1/2 top-full z-50 -translate-x-1/2 ${dropdownWidthClass} max-w-[92vw]`,
                          "invisible opacity-0 pointer-events-none translate-y-2 scale-[0.98] transition-[opacity,transform] duration-200 ease-out",
                          "group-hover:visible group-hover:opacity-100 group-hover:pointer-events-auto group-hover:translate-y-0 group-hover:scale-100",
                          "group-focus-within:visible group-focus-within:opacity-100 group-focus-within:pointer-events-auto group-focus-within:translate-y-0 group-focus-within:scale-100",
                          suppressedDropdownLabel === item.label
                            ? "invisible opacity-0 pointer-events-none translate-y-2 scale-[0.98] duration-100"
                            : "",
                        ].join(" ")}
                        role="menu"
                        aria-label={item.label}
                        style={
                          suppressedDropdownLabel === item.label
                            ? {
                                opacity: 0,
                                pointerEvents: "none",
                              }
                            : undefined
                        }
                      >
                        <div className="rounded-3xl border border-black/[0.06] dark:border-white/[0.08] bg-cyan-100/90 dark:bg-cyan-950/90 p-2 shadow-[0_4px_24px_rgba(0,0,0,0.08),0_1px_3px_rgba(0,0,0,0.04)] dark:shadow-[0_4px_24px_rgba(0,0,0,0.25),0_1px_3px_rgba(0,0,0,0.12)]">
                          <ul className={`grid ${dropdownGridClass} gap-1.5`} role="list">
                            {item.items.map((child) => (
                              <li key={child.href}>
                                <Link
                                  href={child.href}
                                  className={[
                                    "group/card block h-full rounded-2xl border border-black/[0.05] dark:border-white/[0.07] bg-transparent px-3 py-2.5",
                                    "transition-[transform,background-color,box-shadow,border-color,filter] duration-200 ease-out",
                                    "hover:-translate-y-0.5 hover:scale-[1.01] hover:brightness-[1.02] hover:bg-foreground/5",
                                    "hover:border-black/[0.1] dark:hover:border-white/[0.12]",
                                    "hover:shadow-[0_4px_12px_rgba(0,0,0,0.06),0_1px_3px_rgba(0,0,0,0.03)] dark:hover:shadow-[0_4px_12px_rgba(0,0,0,0.2),0_1px_3px_rgba(0,0,0,0.1)]",
                                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
                                  ].join(" ")}
                                  onClick={() => {
                                    setSuppressedDropdownLabel(item.label);
                                    (document.activeElement as HTMLElement | null)?.blur?.();
                                  }}
                                  style={{
                                    backgroundImage: "var(--home-card-gradient)",
                                    backgroundSize: "cover",
                                    backgroundRepeat: "no-repeat",
                                    ...(item.label === "Products" || item.label === "Use Cases"
                                      ? {
                                          width: 329,
                                        }
                                      : null),
                                  }}
                                >
                                  <div className="flex items-start gap-3">
                                    <div className="mt-0.5 flex h-8 w-8 items-center justify-center rounded-full border border-black/[0.06] dark:border-white/[0.1] bg-white dark:bg-white/95 shadow-[0_1px_3px_rgba(0,0,0,0.05)]">
                                      {"icon" in child && child.icon ? (
                                        <child.icon className="h-4 w-4 text-black" aria-hidden />
                                      ) : null}
                                    </div>
                                    <div className="min-w-0">
                                      <div className="text-sm font-semibold text-foreground">{child.label}</div>
                                      {"description" in child && child.description ? (
                                        <div className="mt-0.5 text-xs leading-snug text-muted-foreground">
                                          {child.description}
                                        </div>
                                      ) : null}
                                    </div>
                                  </div>
                                </Link>
                              </li>
                            ))}
                          </ul>
                        </div>
                      </div>
                    </div>
                  ) : isLinkItem(item) ? (
                    <Link
                      href={item.href}
                      className={[
                        "home-nav-link text-[13px] font-medium focus-visible:outline-none",
                        "text-foreground/80 hover:text-foreground",
                      ].join(" ")}
                      aria-current={item.href === "/" ? "page" : undefined}
                      onClick={(event) => {
                        if (!isHome) return;
                        if (!item.href.startsWith("#")) return;
                        event.preventDefault();
                        scrollToHash(item.href);
                      }}
                    >
                      {item.label}
                    </Link>
                  ) : null}
                </li>
              );
            })}
          </ul>

          <div className="navbarThemeWrap flex items-center gap-1.5 lg:gap-2.5 justify-self-end">
            <Link
              href="/dashboard"
              className={[
                "navbarDesktopAction hidden md:inline-flex px-3 text-[13px] font-medium rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-400 transition-[background-color,box-shadow,transform] duration-200 ease-out shadow-[0_2px_8px_rgba(99,102,241,0.25),0_1px_2px_rgba(0,0,0,0.06)] hover:shadow-[0_4px_16px_rgba(99,102,241,0.35),0_2px_4px_rgba(0,0,0,0.08)] hover:scale-[1.02] active:scale-[1.0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
                isCompact ? "py-1" : "py-1.5",
              ].join(" ")}
            >
              Login
            </Link>
            <button
              type="button"
              onClick={toggleTheme}
              className={[
                "inline-flex items-center justify-center rounded-full hover:scale-[1.05] transition-[background-color,transform,color,width,height,box-shadow,border-color] duration-[250ms] ease-in-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
                "text-foreground/80 hover:text-foreground hover:bg-white/10 border border-transparent hover:border-white/[0.1] dark:hover:border-white/[0.12] hover:shadow-[0_2px_8px_rgba(0,0,0,0.08)]",
                isCompact ? "w-8 h-8" : "w-9 h-9",
              ].join(" ")}
              aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              aria-pressed={theme === "dark"}
              title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            >
              {theme === "dark" ? (
                <Sun width={16} height={16} aria-hidden />
              ) : (
                <Moon width={16} height={16} aria-hidden />
              )}
            </button>
          </div>
        </div>
      </div>
      </nav>
    </>
  );
}
