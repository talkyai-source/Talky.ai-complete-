import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "AI for Travel Industry | 24/7 AI Guest Support",
  description:
    "Transform guest experiences with AI for travel industry. Automate bookings, guest support, concierge services, and reservations with AI.",
};

export default function TravelIndustryIndustryPage() {
  const accentCardClassName =
    "group rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6 shadow-sm transition-[transform,filter,border-color,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:brightness-[1.02] hover:border-border hover:shadow-md";
  const accentCardStyle = {
    backgroundImage: "var(--home-card-gradient)",
    backgroundSize: "cover",
    backgroundRepeat: "no-repeat",
  } as const;

  const eyebrowClassName =
    "text-xs sm:text-sm font-semibold uppercase tracking-[0.2em] text-blue-600 dark:text-blue-400";
  const headingClassName = "text-2xl md:text-3xl font-semibold text-primary dark:text-foreground";
  const subHeadingClassName = "text-xl md:text-2xl font-semibold text-primary dark:text-foreground";
  const cardTitleClassName = "text-lg md:text-xl font-semibold text-primary dark:text-foreground";
  const bodyClassName =
    "mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed";
  const cardBodyClassName = "mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed";
  const listClassName = "mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground";
  const pillClassName =
    "rounded-full border border-border/70 bg-background/60 dark:bg-white/5 backdrop-blur-sm px-4 py-2 text-xs sm:text-sm font-medium text-gray-700 dark:text-muted-foreground";
  const pillRowClassName = "mt-8 flex flex-wrap items-center justify-center gap-2 sm:gap-3";
  const buttonSizeClassName = "rounded-full h-12 sm:h-14 px-8 sm:px-10 text-sm sm:text-base font-semibold";
  const primaryButtonClassName = `${buttonSizeClassName} bg-blue-600 hover:bg-blue-700 text-white`;
  const outlineButtonClassName = `${buttonSizeClassName} bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900`;
  const centeredCtaClassName = "mt-10 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4";

  const heroStats = [
    { value: "500K+", label: "Guest Conversations" },
    { value: "24/7", label: "Guest Support" },
    { value: "<2 Sec", label: "Response Time" },
    { value: "99.9%", label: "Uptime" },
  ];

  const guestExperiencePoints = [
    "Instant booking assistance",
    "Fast answers to travel questions",
    "Personalized guest support",
    "24/7 availability",
    "Seamless reservation management",
  ];

  const guestExperiencePills = ["Always Available", "Better Guest Experiences", "Never Miss a Booking"];

  const beforeArrival = [
    {
      title: "Always-On Reservations",
      description:
        "Our AI booking assistant helps guests check availability, confirm reservations, modify bookings, and receive instant confirmations - any time of day.",
    },
    {
      title: "Instant Travel Answers",
      description:
        "Handle AI travel inquiry handling with natural conversations that answer questions about destinations, room types, pricing, amenities, parking, and local attractions.",
    },
    {
      title: "Reservation Management",
      description:
        "Automate AI reservation handling by confirming bookings, processing modifications, managing cancellations, and keeping guests informed without manual follow-ups.",
    },
    {
      title: "Pre-Arrival Welcome",
      description:
        "Send confirmation messages, arrival instructions, and helpful travel information automatically, creating a smooth experience before check-in.",
    },
  ];

  const duringStay = [
    {
      title: "Always Within Reach",
      description:
        "Questions about amenities, dining, hotel facilities, and services are answered instantly through AI hospitality customer service, giving guests the information they need without making them wait.",
    },
    {
      title: "Beyond the Front Desk",
      description:
        "With an AI concierge for hotels, guests can discover local attractions, book transportation, reserve spa treatments, and receive personalized recommendations throughout their stay.",
    },
    {
      title: "Help Without the Hold",
      description:
        "Routine requests such as housekeeping, extra amenities, reservation updates, and general assistance are managed through AI guest support automation, allowing staff to focus on in-person guest experiences.",
    },
    {
      title: "A Language Every Guest Understands",
      description:
        "Welcome international travelers with AI multilingual guest support, providing natural conversations in their preferred language to make every guest feel comfortable and valued.",
    },
    {
      title: "Always Ready to Help",
      description:
        "Powered by AI voice agents for hospitality, every guest call is answered promptly, ensuring consistent service while reducing pressure on your front desk team.",
    },
  ];

  const afterDeparture = [
    {
      title: "Keep the Conversation Going",
      description:
        "Build stronger relationships with AI tourism customer engagement by sending thank-you messages, requesting reviews, and sharing exclusive offers that inspire future stays.",
    },
    {
      title: "Follow Up Effortlessly",
      description:
        "Automatically deliver post-stay surveys, personalized recommendations, and special promotions, helping you stay connected without adding more work for your team.",
    },
    {
      title: "Still Here to Help",
      description:
        "Support billing questions, lost-and-found requests, and post-departure inquiries with fast, reliable assistance that keeps delivering exceptional service after guests leave.",
    },
    {
      title: "Welcome Them Back",
      description:
        "Reconnect with past guests through personalized offers and timely reminders that encourage direct bookings while strengthening long-term guest loyalty.",
    },
  ];

  const businessImpact = [
    {
      title: "More Bookings",
      description:
        "Respond to every guest inquiry instantly, helping convert more travelers into confirmed reservations and reducing missed booking opportunities.",
    },
    {
      title: "Faster Service",
      description:
        "Deliver immediate answers to reservation requests, hotel services, and travel questions, giving guests the quick assistance they expect.",
    },
    {
      title: "Less Front Desk Work",
      description:
        "Handle repetitive guest requests automatically, allowing your staff to focus on meaningful in-person interactions instead of routine tasks.",
    },
    {
      title: "Happier Guests",
      description:
        "Provide consistent, timely communication that creates smooth experiences, strengthens guest satisfaction, and encourages positive reviews.",
    },
    {
      title: "Ready for Peak Seasons",
      description:
        "Manage high call volumes and busy travel periods without adding extra staff or compromising the quality of your guest service.",
    },
    {
      title: "Smarter Operations",
      description:
        "Behind the scenes, AI hospitality workflow automation streamlines reservations, reminders, guest communication, and daily operations from one connected platform.",
    },
  ];

  const hospitalityBusinesses = [
    {
      title: "Hotels",
      description: "Deliver 24/7 guest support, automate reservations, and improve every stage of the guest journey.",
    },
    {
      title: "Luxury Resorts",
      description:
        "Provide premium concierge services, activity bookings, and personalized guest communication without increasing staff workload.",
    },
    {
      title: "Vacation Rentals",
      description:
        "Manage inquiries, booking requests, check-in instructions, and guest communication across multiple properties automatically.",
    },
    {
      title: "Travel Agencies",
      description:
        "Help travelers plan trips, answer destination questions, and manage bookings with faster customer support.",
    },
    {
      title: "Tour Operators",
      description:
        "Automate tour reservations, itinerary updates, scheduling changes, and traveler inquiries while improving response times.",
    },
    {
      title: "Airlines & Transportation",
      description:
        "Support passengers with booking assistance, travel updates, and customer service before, during, and after every journey.",
    },
    {
      title: "Cruise & Tourism Companies",
      description:
        "Deliver personalized guest support, reservation assistance, and travel information throughout every stage of the customer journey.",
    },
  ];

  const reliabilityPills = ["99.9% Uptime", "Enterprise Ready", "Reliable AI", "24/7 Guest Support", "Built to Scale"];

  const whyTeamsChoose = [
    {
      title: "Built for Modern Hospitality",
      description:
        "Designed to help hospitality teams deliver faster service, simplify operations, and create exceptional guest experiences.",
    },
    {
      title: "Never Miss a Call",
      description:
        "Capture every guest inquiry with AI travel call automation, ensuring faster responses and more booking opportunities.",
    },
    {
      title: "Always Open",
      description:
        "An AI virtual receptionist for hotels answers questions, assists with reservations, and supports guests 24/7.",
    },
    {
      title: "Easy Scheduling",
      description:
        "Simplify spa bookings, restaurant reservations, and guest services with AI hotel appointment scheduling.",
    },
    {
      title: "Better Guest Experiences",
      description:
        "Enhance the AI travel customer experience with personalized conversations and faster support throughout every stay.",
    },
  ];

  const integrations = [
    {
      title: "Property Management Systems",
      description:
        "Sync guest profiles, room availability, reservations, and booking updates automatically with your existing PMS.",
    },
    {
      title: "Booking Platforms",
      description: "Keep reservations, availability, and confirmations synchronized across your preferred booking systems.",
    },
    {
      title: "Calendar & Scheduling",
      description:
        "Coordinate spa appointments, restaurant reservations, airport transfers, and other guest services with real-time availability.",
    },
    {
      title: "Workflow Automation",
      description:
        "Automatically send confirmations, reminders, notifications, and internal updates to keep every guest journey running smoothly.",
    },
  ];

  const integrationLogos = ["Oracle OPERA", "Cloudbeds", "Mews", "Google Calendar", "Outlook", "Zapier"];

  const howItWorks = [
    {
      title: "Reserve With Ease",
      description:
        "Guests receive instant answers to booking inquiries, availability, and reservations, making it simple to plan their stay.",
    },
    {
      title: "Personalize Every Stay",
      description:
        "Whether guests need concierge services, hotel information, or travel assistance, every request is understood and handled seamlessly.",
    },
    {
      title: "Support Without Delays",
      description:
        "Routine requests are resolved immediately, while more complex inquiries are routed to the right team with complete context.",
    },
    {
      title: "Stay Connected",
      description:
        "Booking confirmations, reminders, check-in details, and follow-up messages are sent automatically, keeping guests informed at every stage.",
    },
  ];

  const plans = [
    {
      name: "Starter",
      price: "Free / 14-Day Trial",
      blurb: "Perfect for hotels, vacation rentals, and small hospitality businesses exploring AI.",
      features: [
        "One Property Workspace",
        "AI Booking Assistant",
        "Guest Support Automation",
        "Reservation Management",
        "Email Support",
      ],
      ctaLabel: "Start Free",
      ctaHref: "/auth/register",
      ctaVariant: "primary" as const,
    },
    {
      name: "Growth",
      price: "Custom Pricing",
      blurb: "Built for growing hotels, resorts, and travel companies.",
      features: [
        "Multiple Property Workspaces",
        "AI Travel Call Automation",
        "Hospitality Workflow Automation",
        "Booking Platform Integrations",
        "Priority Support",
      ],
      ctaLabel: "Book a Demo",
      ctaHref: "/#contact",
      ctaVariant: "outline" as const,
    },
    {
      name: "Enterprise",
      price: "Let’s Talk",
      blurb: "Designed for hotel groups, resorts, and enterprise travel organizations.",
      features: [
        "Unlimited Properties",
        "Advanced AI Hospitality Workflows",
        "Enterprise Integrations",
        "Dedicated Customer Success Manager",
        "Premium Support",
      ],
      ctaLabel: "Talk to an AI Expert",
      ctaHref: "/#contact",
      ctaVariant: "outline" as const,
    },
  ];

  const faqs = [
    {
      question: "Is this platform built specifically for the travel and hospitality industry?",
      answer:
        "Yes. It’s designed for hotels, resorts, travel agencies, tour operators, vacation rentals, and tourism businesses looking to automate guest communication and improve service quality.",
    },
    {
      question: "Can AI manage reservations and booking inquiries?",
      answer:
        "Absolutely. AI assists guests with availability, reservations, booking modifications, cancellations, and confirmations while providing fast and accurate responses.",
    },
    {
      question: "Can guests receive support in multiple languages?",
      answer:
        "Yes. AI multilingual guest support helps hospitality businesses communicate naturally with international travelers in their preferred language.",
    },
    {
      question: "Can AI act as a virtual receptionist for hotels?",
      answer:
        "Yes. AI answers guest calls, provides hotel information, assists with reservations, and routes specialized requests to the appropriate staff when needed.",
    },
    {
      question: "Does it integrate with our hotel management software?",
      answer:
        "Yes. The platform connects with leading property management systems, booking platforms, calendars, and CRM solutions to keep guest information synchronized.",
    },
    {
      question: "How quickly can we get started?",
      answer:
        "Most hospitality businesses can launch within a few days. Our team handles the setup, allowing you to begin automating guest conversations without lengthy implementation.",
    },
  ];

  return (
    <main className="home-navbar-offset bg-cyan-100 dark:bg-background">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            AI for Travel Industry
          </h1>
          <h2 className="mt-6 text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Never Miss Another Booking or Guest Inquiry
          </h2>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            From reservations and concierge requests to travel questions and guest support, AI ensures every conversation receives
            a fast, accurate, and personalized response.
          </p>
          <div className={centeredCtaClassName}>
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                Book a Demo
              </Button>
            </Link>
            <Link href="/#contact">
              <Button size="lg" variant="outline" className={outlineButtonClassName}>
                See How It Works
              </Button>
            </Link>
          </div>
          <p className="mt-6 text-sm sm:text-base md:text-lg font-semibold text-primary dark:text-foreground">
            Go live with AI that handles bookings and guest inquiries in days.
          </p>
        </header>

        <section className="mt-14">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {heroStats.map((stat) => (
              <div key={stat.label} className={`${accentCardClassName} text-center`} style={accentCardStyle}>
                <p className="text-3xl md:text-4xl font-bold tracking-tight text-primary dark:text-foreground">{stat.value}</p>
                <p className="mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">{stat.label}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>The Guest Experience</p>
          <h2 className={`mt-3 ${headingClassName}`}>Every Missed Call Is a Missed Booking</h2>
          <p className={bodyClassName}>
            Travelers expect quick answers when choosing where to stay. AI responds instantly to booking requests, amenity
            questions, transportation inquiries, and reservation changes, helping you convert more inquiries into confirmed guests.
          </p>
          <p className={bodyClassName}>
            With AI for travel industry, every inquiry is answered instantly. Whether someone wants to book a room, ask about
            amenities, request transportation, or modify a reservation, AI ensures every guest receives fast, friendly, and
            consistent service while your team focuses on delivering memorable experiences.
          </p>
          <ul className={listClassName}>
            {guestExperiencePoints.map((point) => (
              <li key={point}>&bull; {point}</li>
            ))}
          </ul>
          <div className={pillRowClassName}>
            {guestExperiencePills.map((pill) => (
              <span key={pill} className={pillClassName}>
                {pill}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Before Arrival</p>
          <h2 className={`mt-3 ${headingClassName}`}>Make Every First Impression Count</h2>
          <p className={bodyClassName}>
            Guests want quick answers before making a decision. AI responds instantly to reservation requests, availability
            checks, and travel inquiries.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            {beforeArrival.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>During Their Stay</p>
          <h2 className={`mt-3 ${headingClassName}`}>Every Moment Matters</h2>
          <p className={bodyClassName}>
            Whether guests need help, recommendations, or quick answers, every request is handled instantly so your team can focus
            on delivering exceptional hospitality.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {duringStay.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>After They Leave</p>
          <h2 className={`mt-3 ${headingClassName}`}>A Great Stay Should Never Be the Last</h2>
          <p className={bodyClassName}>
            The guest journey doesn&rsquo;t end at check-out. Stay connected with thoughtful follow-ups, personalized
            communication, and memorable experiences that encourage guests to return again and again.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            {afterDeparture.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>The Business Impact</p>
          <h2 className={`mt-3 ${headingClassName}`}>Better Experiences. Better Business.</h2>
          <p className={bodyClassName}>
            Automate routine guest conversations so your team can focus on exceptional hospitality while your business runs more
            efficiently.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {businessImpact.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Built for Every Hospitality Business</p>
          <h2 className={`mt-3 ${headingClassName}`}>Supporting Every Business That Welcomes Guests</h2>
          <p className={bodyClassName}>
            Whether you manage one property or hundreds, AI adapts to your operations and helps deliver exceptional guest
            experiences at every location.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {hospitalityBusinesses.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Built for Hospitality</p>
          <h2 className={`mt-3 ${headingClassName}`}>Reliable AI for Businesses That Never Stop Serving Guests</h2>
          <p className={bodyClassName}>
            Hospitality operates around the clock and so should your customer service. Whether guests call during the day, late at
            night, or across different time zones, AI provides consistent, reliable support whenever it&rsquo;s needed.
          </p>
          <p className={bodyClassName}>
            From boutique hotels to global hospitality brands, every interaction is powered by technology designed to deliver
            dependable service, high availability, and exceptional guest experiences.
          </p>
          <div className={pillRowClassName}>
            {reliabilityPills.map((pill) => (
              <span key={pill} className={pillClassName}>
                {pill}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Why Hospitality Teams Choose Talk-Lee AI</h2>
          <p className={bodyClassName}>Empower your team to deliver outstanding hospitality with less manual work.</p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {whyTeamsChoose.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>One Platform for Every Guest Interaction</h2>
          <p className={bodyClassName}>
            Every guest conversation, reservation, and follow-up is managed from one intelligent platform, helping your team
            deliver seamless hospitality from the first inquiry to the final goodbye.
          </p>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Integrations</p>
          <h2 className={`mt-3 ${headingClassName}`}>Connect With the Hospitality Tools You Already Use</h2>
          <p className={bodyClassName}>
            Keep the systems you trust while connecting reservations, guest information, calendars, and daily operations into one
            connected workflow.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            {integrations.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
          <div className={pillRowClassName}>
            {integrationLogos.map((logo) => (
              <span key={logo} className={pillClassName}>
                {logo}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>How It Works</h2>
          <p className={bodyClassName}>From booking to check-out, every interaction is handled with speed, consistency, and care.</p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {howItWorks.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Flexible Plans</p>
          <h2 className={`mt-3 ${headingClassName}`}>Pricing for Every Travel &amp; Hospitality Business</h2>
          <p className={bodyClassName}>
            Whether you operate a boutique hotel or a global hospitality brand, choose a plan designed to automate guest
            communication and grow with your business.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-4">
            {plans.map((plan) => (
              <div key={plan.name} className={`${accentCardClassName} flex flex-col`} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{plan.name}</h3>
                <p className="mt-3 text-2xl md:text-3xl font-bold tracking-tight text-primary dark:text-foreground">
                  {plan.price}
                </p>
                <p className={cardBodyClassName}>{plan.blurb}</p>
                <ul className="mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
                  {plan.features.map((feature) => (
                    <li key={feature}>&bull; {feature}</li>
                  ))}
                </ul>
                <div className="mt-8 flex flex-1 items-end justify-center">
                  <Link href={plan.ctaHref}>
                    {plan.ctaVariant === "primary" ? (
                      <Button size="lg" className={primaryButtonClassName}>
                        {plan.ctaLabel}
                      </Button>
                    ) : (
                      <Button size="lg" variant="outline" className={outlineButtonClassName}>
                        {plan.ctaLabel}
                      </Button>
                    )}
                  </Link>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Frequently Asked Questions</h2>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            {faqs.map((faq) => (
              <div key={faq.question} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{faq.question}</h3>
                <p className={cardBodyClassName}>{faq.answer}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <div className="rounded-3xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm p-8 md:p-12 text-center shadow-sm transition-[transform,box-shadow,border-color] duration-200 ease-out hover:-translate-y-0.5 hover:shadow-md hover:border-border">
            <h2 className={subHeadingClassName}>Create Five-Star Guest Experiences&mdash;Before Guests Even Arrive</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Turn every guest inquiry into a booking with AI-powered reservations, support, and concierge services.
            </p>
            <div className={centeredCtaClassName}>
              <Link href="/auth/register">
                <Button size="lg" className={primaryButtonClassName}>
                  Book a Demo
                </Button>
              </Link>
              <Link href="/#contact">
                <Button size="lg" variant="outline" className={outlineButtonClassName}>
                  Explore Hospitality Plans
                </Button>
              </Link>
            </div>
          </div>
        </section>
      </div>
      <Footer />
    </main>
  );
}
