import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "AI for Retail & E-commerce | AI Customer Support",
  description:
    "Talk-Lee AI for retail and e-commerce that handles customer calls, product inquiries, order support, and follow-ups 24/7. Improve customer service with Talk-Lee AI.",
};

export default function RetailEcommerceIndustryPage() {
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
  const cardTitleClassName = "text-lg md:text-xl font-semibold text-primary dark:text-foreground";
  const bodyClassName =
    "mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed";
  const cardBodyClassName = "mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed";
  const listClassName = "mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground";
  const pillClassName =
    "rounded-full border border-border/70 bg-background/60 dark:bg-white/5 backdrop-blur-sm px-4 py-2 text-xs sm:text-sm font-medium text-gray-700 dark:text-muted-foreground";
  const buttonSizeClassName = "rounded-full h-12 sm:h-14 px-8 sm:px-10 text-sm sm:text-base font-semibold";
  const primaryButtonClassName = `${buttonSizeClassName} bg-blue-600 hover:bg-blue-700 text-white`;
  const outlineButtonClassName = `${buttonSizeClassName} bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900`;
  const centeredCtaClassName = "mt-10 flex justify-center";
  const centeredCtaPairClassName = "mt-10 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4";

  const instantSupportCapabilities = [
    "Handle product questions",
    "Provide order information",
    "Support returns and common requests",
    "Capture customer details",
    "Schedule appointments",
    "Route complex conversations",
    "Follow up with customers",
  ];

  const instantSupportStats = ["24/7 Availability", "Instant Support", "1000+ Happier Customers"];

  const completedActions = [
    {
      title: "AI Retail Customer Support",
      description:
        "Handle common customer questions automatically and provide fast, conversational support without forcing customers through complicated phone menus.",
    },
    {
      title: "AI Order Inquiry Automation",
      description:
        "Help customers with order-related questions, including order status, delivery updates, shipping information, and other supported requests.",
    },
    {
      title: "AI Product Inquiry Handling",
      description:
        "Answer questions about products, features, availability, sizing, pricing, and other information based on your approved business and product data.",
    },
    {
      title: "AI Voice Agents for Retail",
      description:
        "Give customers a natural voice experience that feels conversational rather than scripted, while handling high volumes of inbound and outbound calls.",
    },
    {
      title: "AI Appointment Booking for Retail",
      description:
        "Allow customers to schedule consultations, store appointments, services, pickups, or other available bookings based on your business workflow.",
    },
  ];

  const readyToBuy = [
    {
      title: "Respond Instantly",
      description: "Give customers immediate answers instead of sending them to voicemail or making them wait.",
    },
    {
      title: "Know Their Needs",
      description: "Understand whether they’re asking about a product, order, delivery, availability, or support.",
    },
    {
      title: "Build Confidence",
      description: "Provide relevant information that helps customers make faster, more informed decisions.",
    },
    {
      title: "Drive the Next Step",
      description: "Guide customers toward a purchase, appointment, follow-up, or connection with your team.",
    },
  ];

  const fewerCalls = [
    {
      title: "Cut the Repetition",
      description: "Automate common questions and routine requests that take up valuable team time.",
    },
    {
      title: "Respond Faster",
      description: "Give customers instant answers instead of making them wait for an available representative.",
    },
    {
      title: "Smarter Handoffs",
      description: "Pass relevant conversation context to your team when human support is needed.",
    },
    {
      title: "Handle Peak Demand",
      description: "Stay responsive during promotions, product launches, holidays, and high-volume periods.",
    },
  ];

  const customerJourney = [
    {
      title: "Find Products",
      description:
        "Help customers discover the right products with answers about features, options, pricing, and availability.",
    },
    {
      title: "Track Orders",
      description: "Handle common questions about order status, shipping, delivery, and post-purchase updates.",
    },
    {
      title: "Handle Returns",
      description: "Guide customers through return and exchange questions based on your business policies.",
    },
    {
      title: "Share Store Info",
      description: "Instantly answer questions about locations, hours, services, and store availability.",
    },
    {
      title: "Stay Connected",
      description: "Follow up after inquiries, purchases, appointments, or support conversations.",
    },
    {
      title: "Bring in Experts",
      description: "Route complex requests to the right team member with useful context already captured.",
    },
  ];

  const keepEngaged = [
    {
      title: "Recover Missed Calls",
      description:
        "Reconnect with customers who called when your team was unavailable or couldn’t complete the conversation.",
    },
    {
      title: "Follow Up Orders",
      description:
        "Keep customers informed through supported follow-up workflows when an order requires additional communication.",
    },
    {
      title: "Re-Engage Leads",
      description: "Follow up with prospects who asked questions but didn’t complete the next step.",
    },
    {
      title: "Support After Purchase",
      description:
        "Continue communication after purchases, appointments, or service interactions to create a smoother customer experience.",
    },
  ];

  const callingExperience = [
    {
      title: "Speak Naturally",
      description: "Customers explain what they need without navigating confusing menus or repeating information.",
    },
    {
      title: "Understand Instantly",
      description: "Talk-Lee AI identifies the purpose of the call and captures the details needed to help.",
    },
    {
      title: "Answer Clearly",
      description: "Provide relevant responses using your approved product, order, and business information.",
    },
    {
      title: "Guide the Customer",
      description: "Move conversations toward order updates, appointments, follow-ups, or other supported actions.",
    },
    {
      title: "Connect When Needed",
      description: "Route complex conversations to the right team member with useful context already captured.",
    },
  ];

  const aroundTheClock = ["Morning Orders", "Peak Shopping Hours", "Evening Questions", "Weekend Support"];

  const conversationInsights = [
    "Customer Information",
    "Order Details",
    "Product Interests",
    "Support Requests",
    "Appointment Details",
    "Follow-Up Actions",
    "Lead Routing",
  ];

  const whyRetailTeams = [
    "Retail-Focused AI",
    "Natural Conversations",
    "Faster Customer Journeys",
    "Actionable Call Insights",
    "Flexible Workflows",
    "Easy Team Escalation",
    "Peak-Time Ready",
    "Consistent Brand Experience",
  ];

  const conversationsIntoAction = [
    { title: "Customer Data", description: "Capture useful information from every conversation." },
    {
      title: "Order Workflows",
      description: "Connect supported order-related conversations to your existing processes.",
    },
    { title: "Product Information", description: "Give AI access to approved product and business information." },
    {
      title: "Calendar Coordination",
      description: "Support appointment and booking workflows based on available schedules.",
    },
    { title: "Lead Routing", description: "Send important conversations to the right team or representative." },
    { title: "Follow-Up", description: "Trigger supported follow-up actions after customer interactions." },
  ];

  const howItWorks = [
    {
      title: "Answer",
      description: "The AI picks up instantly and gives the customer a professional, conversational first response.",
    },
    {
      title: "Understand",
      description: "It identifies the customer's reason for calling and gathers the relevant information.",
    },
    {
      title: "Assist",
      description:
        "Talk-Lee AI answers supported questions, provides information, and helps complete the appropriate next action.",
    },
    {
      title: "Resolve",
      description:
        "Routine conversations can be handled automatically while more complex requests can move to your team.",
    },
    {
      title: "Follow Up",
      description: "Supported workflows can continue the conversation after the initial interaction.",
    },
  ];

  const everythingYouNeed = [
    "AI Retail Customer Support",
    "AI Order Inquiry Automation",
    "AI Voice Agents for Retail",
    "AI Customer Engagement",
    "AI Product Inquiry Handling",
    "AI Retail Workflow Automation",
    "AI Omnichannel Customer Support",
    "AI Virtual Assistant for E-commerce",
    "AI Inbound Retail Calls",
    "AI Retail Call Center",
  ];

  const retailModels = [
    {
      title: "E-commerce Stores",
      description:
        "Handle product questions, order inquiries, customer support, and follow-up without relying entirely on manual phone support.",
    },
    {
      title: "DTC Brands",
      description: "Give customers a direct, conversational support channel while maintaining your brand experience.",
    },
    {
      title: "Retail Stores",
      description:
        "Answer store-related questions, product inquiries, appointments, and customer requests even outside regular hours.",
    },
    {
      title: "Multi-Location Retailers",
      description: "Route customers to the right location, team, or department based on their needs.",
    },
    {
      title: "High-Volume Retailers",
      description: "Handle increased call demand during promotions, launches, holidays, and seasonal peaks.",
    },
    {
      title: "Service-Based Retail",
      description: "Support bookings, consultations, appointments, customer questions, and follow-up conversations.",
    },
  ];

  const faqs = [
    {
      question: "What can AI do for retail customer support?",
      answer:
        "AI can answer calls, handle product inquiries, support order-related questions, schedule appointments, route conversations, and assist with follow-up workflows.",
    },
    {
      question: "Can AI handle e-commerce order inquiries?",
      answer:
        "Yes. When connected to the appropriate order information and workflows, AI can assist customers with supported questions about orders, shipping, delivery, and related requests.",
    },
    {
      question: "Can AI answer product questions?",
      answer:
        "Yes. AI can provide answers based on your approved product catalog and business information, including supported questions about features, availability, pricing, sizing, and products.",
    },
    {
      question: "Can AI make outbound retail calls?",
      answer:
        "Yes. Depending on your workflow, AI can support outbound conversations such as customer follow-ups, order-related communication, appointment reminders, and other approved use cases.",
    },
    {
      question: "Can AI schedule retail appointments?",
      answer:
        "Yes. Talk-Lee AI can support appointment booking for consultations, services, store appointments, pickups, and other scheduled activities based on your setup.",
    },
    {
      question: "Can AI transfer customers to human agents?",
      answer:
        "Yes. When a conversation requires human expertise, the AI can route the customer to the appropriate team member with relevant conversation context.",
    },
    {
      question: "Does AI work after business hours?",
      answer:
        "Yes. Our AI agent can remain available 24/7, allowing customers to reach your business outside normal operating hours.",
    },
    {
      question: "Can we support multiple channels?",
      answer:
        "Talk-Lee AI can fit into broader customer workflows involving voice, customer records, calendars, and follow-up processes, depending on your business setup and integrations.",
    },
  ];

  return (
    <main className="home-navbar-offset bg-cyan-50 dark:bg-black">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            AI for Retail &amp; E-commerce
          </h1>
          <h2 className="mt-6 text-xl md:text-2xl font-semibold text-primary dark:text-foreground">
            Turn Customer Calls Into Sales, Support, and Loyalty
          </h2>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Give every customer a faster way to get answers with Talk-Lee AI. Handle calls, product questions, order
            inquiries, and support conversations 24/7.
          </p>
          <div className={centeredCtaPairClassName}>
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                See AI Handle a Retail Call
              </Button>
            </Link>
            <Link href="/#contact">
              <Button size="lg" variant="outline" className={outlineButtonClassName}>
                Book a Demo
              </Button>
            </Link>
          </div>
          <p className="mt-8 text-base sm:text-lg font-semibold text-primary dark:text-foreground">
            Give every customer a faster way to get answers and take action.
          </p>
        </header>

        <section className="mt-14">
          <p className={eyebrowClassName}>Built for Modern Retail</p>
          <h2 className={`mt-3 ${headingClassName}`}>Customers Expect Instant Support</h2>
          <p className={bodyClassName}>
            Retail customers don&rsquo;t wait for business hours. Whether they&rsquo;re checking an order, asking about a
            product, or looking for help, they expect quick, convenient answers.
          </p>
          <p className={bodyClassName}>
            Missed calls and slow responses can mean frustrated customers, abandoned purchases, and lost loyalty. Talk-Lee
            AI keeps your business available 24/7, handling routine conversations instantly and bringing your team in when
            human support matters.
          </p>
          <ul className={listClassName}>
            {instantSupportCapabilities.map((item) => (
              <li key={item}>&bull; {item}</li>
            ))}
          </ul>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-4">
            {instantSupportStats.map((stat) => (
              <div key={stat} className={`${accentCardClassName} text-center`} style={accentCardStyle}>
                <p className="text-base sm:text-lg font-semibold text-primary dark:text-foreground">{stat}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Built for Retail &amp; E-commerce</p>
          <h2 className={`mt-3 ${headingClassName}`}>From Customer Question to Completed Action</h2>
          <p className={bodyClassName}>
            Talk-Lee AI does more than answer the phone. It understands what customers need and helps move each
            conversation toward the right outcome.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {completedActions.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Turn Customer Interest Into Action</p>
          <h2 className={`mt-3 ${headingClassName}`}>Respond While They&rsquo;re Ready to Buy</h2>
          <p className={bodyClassName}>
            Customers often reach out when they&rsquo;re already considering a purchase. A slow response can give them a
            reason to leave and shop elsewhere. Talk-Lee AI responds instantly, answers questions, and keeps the buying
            journey moving.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {readyToBuy.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Give Your Team Fewer Calls to Handle</h2>
          <p className={bodyClassName}>
            Free your support team from repetitive calls and routine questions so they can focus on complex issues,
            customer relationships, and revenue-driving conversations.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {fewerCalls.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>One AI Assistant Across Your Customer Journey</h2>
          <p className={bodyClassName}>
            Talk-Lee AI stays with your customers throughout the buying journey, helping them discover products, get
            support, complete orders, and stay connected after the sale.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {customerJourney.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Keep Customers Engaged After the First Call</h2>
          <p className={bodyClassName}>
            Customers may need another interaction before they purchase, complete an order, attend an appointment, or
            resolve an issue. Talk-Lee AI helps businesses maintain that connection.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {keepEngaged.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Retail Calling Experience</p>
          <h2 className={`mt-3 ${headingClassName}`}>Conversations That Move Customers Forward</h2>
          <p className={bodyClassName}>
            Give customers a simpler way to get help. Talk-Lee AI listens naturally, understands their needs, provides
            relevant answers, and guides each conversation toward the right outcome.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-4">
            {callingExperience.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Never Out of Reach</p>
          <h2 className={`mt-3 ${headingClassName}`}>Keep Serving Customers Around the Clock</h2>
          <p className={bodyClassName}>
            Customers shop and seek support at all hours. An after-hours question shouldn&rsquo;t automatically become a
            lost sale or frustrated customer.
          </p>
          <p className={bodyClassName}>
            Talk-Lee AI keeps your voice channel available when your team is busy, offline, or handling other customers.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {aroundTheClock.map((item) => (
              <span key={item} className={pillClassName}>
                {item}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Every Conversation Can Improve the Next One</h2>
          <p className={bodyClassName}>
            Every customer conversation reveals what people need, what they&rsquo;re asking for, and where your business
            can improve. Talk-Lee AI captures valuable details and connects them with your existing workflows.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              {conversationInsights.map((item) => (
                <li key={item}>&bull; {item}</li>
              ))}
            </ul>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Why Retail Teams Choose Talk-Lee AI</h2>
          <p className={bodyClassName}>
            Talk-Lee AI combines intelligent voice conversations with practical retail workflows to help businesses
            respond faster, serve customers better, and handle more conversations with less manual effort.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              {whyRetailTeams.map((item) => (
                <li key={item}>&bull; {item}</li>
              ))}
            </ul>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Turn Customer Conversations Into Action</h2>
          <p className={bodyClassName}>
            Talk-Lee AI can fit into workflows involving customer records, calendars, order information, product data, and
            follow-up processes, depending on your setup.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {conversationsIntoAction.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>How It Works</p>
          <h2 className={`mt-3 ${headingClassName}`}>From Incoming Call to Customer Action</h2>
          <p className={bodyClassName}>
            Talk-Lee AI turns a customer call into a structured conversation that can lead to an answer, action, or human
            handoff.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-4">
            {howItWorks.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Everything Your Retail Team Needs</h2>
          <p className={bodyClassName}>
            Give your business an AI voice layer designed to handle customer conversations, support operations, and sales
            opportunities.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {everythingYouNeed.map((item) => (
              <span key={item} className={pillClassName}>
                {item}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Built for Modern Retail Businesses</p>
          <h2 className={`mt-3 ${headingClassName}`}>One AI Layer for Different Retail Models</h2>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {retailModels.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>The AI Advantage for Retail &amp; E-commerce</h2>
          <p className={bodyClassName}>
            Talk-Lee AI helps retail and e-commerce businesses respond faster, automate routine conversations, support
            customers around the clock, and keep more opportunities moving toward action.
          </p>
          <div className={centeredCtaClassName}>
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                Start Automating Customer Calls
              </Button>
            </Link>
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
            <h2 className={headingClassName}>Your Next Customer Could Be Calling</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Don&rsquo;t let unanswered calls become lost sales or frustrated customers. Let Talk-Lee AI answer, assist,
              qualify, schedule, and keep customer conversations moving.
            </p>
            <div className={centeredCtaPairClassName}>
              <Link href="/auth/register">
                <Button size="lg" className={primaryButtonClassName}>
                  Book a Demo
                </Button>
              </Link>
              <Link href="/#contact">
                <Button size="lg" variant="outline" className={outlineButtonClassName}>
                  See AI in Action
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
