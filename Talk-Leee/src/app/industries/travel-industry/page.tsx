import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";
import { pageTitle } from "@/lib/site-metadata";

export const metadata: Metadata = {
  title: pageTitle("AI Voice Agents for Travel and Hospitality"),
  description:
    "Answer guest calls from approved hotel and travel information and collect booking requests for your team. A captured request does not confirm a booking.",
};

export default function TravelIndustryIndustryPage() {
  const accentCardClassName =
    "group rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6 shadow-sm transition-[transform,filter,border-color,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:after:absolute hover:after:inset-x-0 hover:after:top-full hover:after:box-content hover:after:h-0.5 hover:after:border-transparent hover:after:[border-bottom-width:inherit] hover:after:content-[''] hover:brightness-[1.02] hover:border-border hover:shadow-md";
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
  const buttonSizeClassName = "rounded-full min-h-12 sm:min-h-14 h-auto whitespace-normal text-center max-w-full px-8 sm:px-10 text-sm sm:text-base font-semibold";
  const primaryButtonClassName = `${buttonSizeClassName} bg-blue-600 hover:bg-blue-700 text-white`;
  const outlineButtonClassName = `${buttonSizeClassName} bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900`;
  const centeredCtaClassName = "mt-10 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4";

  const heroStats = [
    { value: "Guest", label: "Enquiries" },
    { value: "Configured", label: "Hours" },
    { value: "Request", label: "Details" },
    { value: "Call", label: "History" },
  ];

  const guestExperiencePoints = [
    "Booking request intake",
    "Answers to travel questions",
    "Answers from approved information",
    "Configured calling hours",
    "Reservation requests for your team",
  ];

  const guestExperiencePills = ["Configured Hours", "Guest Enquiries", "Booking Requests"];

  const beforeArrival = [
    {
      title: "Booking Request Capture",
      description:
        "Collect booking requests for your team to review. The agent does not book into a calendar or check live availability.",
    },
    {
      title: "Approved Travel Answers",
      description:
        "Handle AI travel inquiry handling with natural conversations that answer questions about destinations, room types, pricing, amenities, parking, and local attractions.",
    },
    {
      title: "Reservation Requests",
      description:
        "Collect booking change and cancellation requests for your team. A captured request does not confirm a change.",
    },
    {
      title: "Pre-Arrival Welcome",
      description:
        "Send a follow-up email with arrival details once Gmail is connected and the campaign has an email action.",
    },
  ];

  const duringStay = [
    {
      title: "Always Within Reach",
      description:
        "Use approved information about amenities, dining, hotel facilities and services to answer routine guest questions.",
    },
    {
      title: "Beyond the Front Desk",
      description:
        "Answer guest questions about local attractions and hotel services from approved information. The agent does not book transport or spa treatments.",
    },
    {
      title: "Help Without the Hold",
      description:
        "Take routine requests such as housekeeping or extra amenities and pass them to your staff to act on, so they can focus on guests in person.",
    },
    {
      title: "English by Default",
      description:
        "Speech recognition is set to English by default, and there is no language setting.",
    },
    {
      title: "Always Ready to Help",
      description:
        "Guest calls are answered within your configured hours and account call limits, which takes routine calls off your front desk team.",
    },
  ];

  const afterDeparture = [
    {
      title: "Follow-up Calls",
      description:
        "Run an outgoing campaign to thank past guests and ask about their stay.",
    },
    {
      title: "Follow Up by Email",
      description:
        "Send a follow-up email once Gmail is connected. Surveys and promotions are not sent by Talk-Lee AI.",
    },
    {
      title: "Still Here to Help",
      description:
        "Take billing questions and lost-and-found requests from past guests and collect them for your team.",
    },
    {
      title: "Welcome Them Back",
      description:
        "Call past guests on an outgoing campaign to tell them about your offers.",
    },
  ];

  const businessImpact = [
    {
      title: "Booking Requests",
      description:
        "Collect guest enquiries and booking requests for your team to review.",
    },
    {
      title: "Faster Service",
      description:
        "Answer reservation and travel questions from approved information, within your configured hours.",
    },
    {
      title: "Less Front Desk Work",
      description:
        "Answer repetitive guest questions from approved information, so your staff can focus on guests in person.",
    },
    {
      title: "Happier Guests",
      description:
        "Answer guest calls promptly within your configured hours and account call limits.",
    },
    {
      title: "Ready for Peak Seasons",
      description:
        "Answer routine calls in busy periods, within your account call limits.",
    },
    {
      title: "Call History",
      description:
        "Review call history and captured requests in the dashboard, and log calls to HubSpot or Salesforce once connected.",
    },
  ];

  const hospitalityBusinesses = [
    {
      title: "Hotels",
      description: "Answer guest calls in your configured hours and collect reservation requests for your team.",
    },
    {
      title: "Luxury Resorts",
      description:
        "Answer guest questions about activities and services from approved information, and collect requests.",
    },
    {
      title: "Vacation Rentals",
      description:
        "Answer inquiries and collect booking requests, with approved check-in information, for each property.",
    },
    {
      title: "Travel Agencies",
      description:
        "Answer destination questions and collect trip requests for your agents to follow up.",
    },
    {
      title: "Tour Operators",
      description:
        "Answer tour questions from approved information and collect reservation requests for your team.",
    },
    {
      title: "Airlines & Transportation",
      description:
        "Answer passenger questions from approved information and collect requests for your team.",
    },
    {
      title: "Cruise & Tourism Companies",
      description:
        "Answer guest questions from approved information and collect reservation requests.",
    },
  ];

  const reliabilityPills = ["Call History", "Campaign Settings", "Voice Options", "Configured Hours", "Call Limits"];

  const whyTeamsChoose = [
    {
      title: "Built for Modern Hospitality",
      description:
        "Designed to help hospitality teams deliver faster service, simplify operations, and create exceptional guest experiences.",
    },
    {
      title: "Configured Call Intake",
      description:
        "Capture guest enquiries within your configured hours and account call limits.",
    },
    {
      title: "Front Desk Calls",
      description:
        "An AI voice agent answers hotel questions from approved information and collects reservation requests.",
    },
    {
      title: "Request Capture",
      description:
        "Collect spa, restaurant and guest service requests for your staff to arrange. Calls do not book into a calendar.",
    },
    {
      title: "Better Guest Experiences",
      description:
        "Enhance the AI travel customer experience with personalized conversations and faster support throughout every stay.",
    },
  ];

  const connectors = [
    {
      title: "Call Logging",
      description:
        "Once you connect HubSpot or Salesforce (Business plan), calls are logged there. Google Calendar is used by the dashboard assistant, not by calls.",
    },
    {
      title: "Follow-up Email",
      description:
        "Send a follow-up email once Gmail is connected and the campaign has an email action.",
    },
  ];

  const connectorLogos = ["HubSpot or Salesforce"];

  const howItWorks = [
    {
      title: "Reserve With Ease",
      description:
        "Use approved booking information and collect guest preferences for review.",
    },
    {
      title: "Personalize Every Stay",
      description:
        "Whether guests need hotel information or travel assistance, the agent answers from approved information or collects the request.",
    },
    {
      title: "Requests for Review",
      description:
        "Use approved information for routine enquiries and collect requests for review. Live transfer to a person is available when it is set up for the campaign.",
    },
    {
      title: "Stay Connected",
      description:
        "A follow-up email can be sent once Gmail is connected and the campaign has an email action.",
    },
  ];

  const faqs = [
    {
      question: "Is this platform built specifically for the travel and hospitality industry?",
      answer:
        "It can be set up for hotels, resorts, travel agencies, tour operators, vacation rentals and tourism businesses, using the information you approve.",
    },
    {
      question: "Can AI manage reservations?",
      answer:
        "AI answers booking questions from approved information and collects requests. It does not confirm, change or cancel reservations.",
    },
    {
      question: "Can guests get support in other languages?",
      answer:
        "No. Speech recognition is set to English by default, and there is no language setting.",
    },
    {
      question: "Can AI act as a virtual receptionist for hotels?",
      answer:
        "AI can use approved hotel information and collect guest requests for review. Live transfer to a person is available when it is set up for the campaign.",
    },
    {
      question: "How quickly can we get started?",
      answer:
        "Setup depends on your phone line, approved knowledge and campaign. Review these before you start calling.",
    },
  ];

  return (
    <main className="home-navbar-offset bg-cyan-50 dark:bg-black">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            AI for Travel Industry
          </h1>
          <h2 className="mt-6 text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Collect Booking and Guest Enquiries
          </h2>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            From reservations and concierge requests to travel questions and guest support, AI answers guest calls from
            approved information and collects requests for your team to review.
          </p>
          <div className={centeredCtaClassName}>
            <Button asChild size="lg" className={primaryButtonClassName}>
              <Link href="/book-a-demo">
                Book a Demo
              </Link>
            </Button>
            <Button asChild size="lg" variant="outline" className={outlineButtonClassName}>
              <Link href="/auth/register">
                Get Started
              </Link>
            </Button>
          </div>
          <p className="mt-6 text-sm sm:text-base md:text-lg font-semibold text-primary dark:text-foreground">
            Calls need a paid plan or Pay as you go minutes. See pricing for plans.
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
            Travelers may ask about bookings, amenities, transportation or reservation changes. Configure approved answers
            and collect requests for your team to review.
          </p>
          <p className={bodyClassName}>
            Talk-Lee AI handles configured calls within your account and provider limits. A captured request does not
            confirm a booking, reservation change or transportation arrangement.
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
            Use approved travel information and capture reservation requests and guest preferences for review.
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
            Use approved information for routine guest questions and collect requests for your team to review.
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
            The guest journey doesn&rsquo;t end at check-out. Stay connected with outgoing follow-up calls, and with email once Gmail is connected.
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
          <h2 className={`mt-3 ${headingClassName}`}>Configured Hours for Businesses That Serve Guests</h2>
          <p className={bodyClassName}>
            Guests call in the day, late at night and from other time zones. You choose the hours the agent answers
            and how calls outside them are handled.
          </p>
          <p className={bodyClassName}>
            Every call follows the campaign settings, voice options and call limits you configure.
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
          <h2 className={headingClassName}>One Voice Agent for Guest Calls</h2>
          <p className={bodyClassName}>
            Guest calls, captured requests and follow-up emails are managed from one dashboard, helping your team
            follow up on every request from the first enquiry.
          </p>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Connectors</p>
          <h2 className={`mt-3 ${headingClassName}`}>Connect the Tools Talk-Lee AI Supports</h2>
          <p className={bodyClassName}>
            Connect HubSpot or Salesforce to log calls, and Gmail to send follow-up emails.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            {connectors.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
          <div className={pillRowClassName}>
            {connectorLogos.map((logo) => (
              <span key={logo} className={pillClassName}>
                {logo}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>How It Works</h2>
          <p className={bodyClassName}>Calls follow the campaign you configure, using approved information and enabled actions.</p>
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
          <div className="rounded-3xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm p-8 md:p-12 text-center shadow-sm transition-[transform,box-shadow,border-color] duration-200 ease-out hover:-translate-y-0.5 hover:after:absolute hover:after:inset-x-0 hover:after:top-full hover:after:box-content hover:after:h-0.5 hover:after:border-transparent hover:after:[border-bottom-width:inherit] hover:after:content-[''] hover:shadow-md hover:border-border">
            <h2 className={subHeadingClassName}>Answer Guest Calls&mdash;Before Guests Even Arrive</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Capture every guest enquiry and booking request for your team, within your configured hours.
            </p>
            <div className={centeredCtaClassName}>
              <Button asChild size="lg" className={primaryButtonClassName}>
                <Link href="/book-a-demo">
                  Book a Demo
                </Link>
              </Button>
              <Button asChild size="lg" variant="outline" className={outlineButtonClassName}>
                <Link href="/pricing">
                  View Pricing
                </Link>
              </Button>
            </div>
          </div>
        </section>
      </div>
      <Footer />
    </main>
  );
}
