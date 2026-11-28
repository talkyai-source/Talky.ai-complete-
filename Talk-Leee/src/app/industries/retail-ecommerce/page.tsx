import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";
import { pageTitle } from "@/lib/site-metadata";

export const metadata: Metadata = {
  title: pageTitle("AI Voice Agents for Retail & E-commerce"),
  description:
    "How retail and e-commerce teams can use AI voice agents to answer routine product and store questions and collect customer requests for review.",
};

export default function RetailEcommerceIndustryPage() {
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
    "Share approved order policies",
    "Answer return policy questions",
    "Capture customer details",
    "Schedule callbacks",
    "Capture requests for team review",
    "Send follow-up emails when set up",
  ];

  const instantSupportStats = ["Configured Hours", "Enquiry Intake", "Call History"];

  const completedActions = [
    {
      title: "AI Retail Customer Support",
      description:
        "Handle common customer questions automatically and provide fast, conversational support without forcing customers through complicated phone menus.",
    },
    {
      title: "AI Order Inquiry Automation",
      description:
        "Answer order-related questions from your approved information, such as shipping and delivery policies. Individual orders are not looked up.",
    },
    {
      title: "AI Product Inquiry Handling",
      description:
        "Answer questions about products, features, sizing, pricing, and other information based on your approved business and product data.",
    },
    {
      title: "AI Voice Agents for Retail",
      description:
        "Give customers a natural voice experience that feels conversational rather than scripted, while handling inbound and outbound calls within your account and provider limits.",
    },
    {
      title: "AI Callback Scheduling for Retail",
      description:
        "Let customers ask for a callback, and schedule it when callbacks are among the campaign’s approved actions.",
    },
  ];

  const readyToBuy = [
    {
      title: "Use Approved Answers",
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
      description: "Guide customers toward a purchase, a callback, a follow-up, or a request for your team.",
    },
  ];

  const fewerCalls = [
    {
      title: "Cut the Repetition",
      description: "Automate common questions and routine requests that take up valuable team time.",
    },
    {
      title: "Respond Faster",
      description: "Use approved product and business information to answer routine questions.",
    },
    {
      title: "Requests for Review",
      description: "Review captured customer requests. Live transfer to a person is available when it is set up for the campaign.",
    },
    {
      title: "Handle Peak Demand",
      description: "Answer routine calls during promotions, launches and holidays, within your account and provider limits.",
    },
  ];

  const customerJourney = [
    {
      title: "Find Products",
      description:
        "Help customers discover the right products with answers about features, options, and pricing.",
    },
    {
      title: "Answer Order Questions",
      description: "Handle common questions about shipping and delivery policies; order lookup is not available.",
    },
    {
      title: "Handle Returns",
      description: "Guide customers through return and exchange questions based on your business policies.",
    },
    {
      title: "Share Store Info",
      description: "Use approved information about locations, hours and services.",
    },
    {
      title: "Stay Connected",
      description: "Follow up after enquiries or support conversations by email when Gmail is connected.",
    },
    {
      title: "Review Complex Requests",
      description: "Collect requests for your team to review and decide the next step.",
    },
  ];

  const keepEngaged = [
    {
      title: "Capture Missed Requests",
      description:
        "Capture requests from customers who called when your team was unavailable or couldn’t complete the conversation.",
    },
    {
      title: "Follow-Up Emails",
      description:
        "Send a follow-up email after a call when Gmail is connected and the campaign allows it.",
    },
    {
      title: "Re-Engage Leads",
      description: "Follow up with prospects who asked questions but didn’t complete the next step.",
    },
    {
      title: "Support After Purchase",
      description:
        "Continue communication after purchases or service interactions with campaign calls you configure.",
    },
  ];

  const callingExperience = [
    {
      title: "Speak Naturally",
      description: "Customers explain what they need without navigating confusing menus or repeating information.",
    },
    {
      title: "Collect the Request",
      description: "Talk-Lee AI identifies the purpose of the call and captures the details needed to help.",
    },
    {
      title: "Answer Clearly",
      description: "Provide relevant responses using your approved product, order, and business information.",
    },
    {
      title: "Guide the Customer",
      description: "Move conversations toward callbacks, follow-ups, or other actions enabled for the campaign.",
    },
    {
      title: "Review Requests",
      description: "Review captured requests. Live transfer to a person is available when it is set up for the campaign.",
    },
  ];

  const aroundTheClock = ["Morning Orders", "Peak Shopping Hours", "Evening Questions", "Weekend Support"];

  const conversationInsights = [
    "Customer Information",
    "Order Questions",
    "Product Interests",
    "Support Requests",
    "Callback Details",
    "Follow-Up Actions",
    "Lead Details",
  ];

  const whyRetailTeams = [
    "Retail-Focused AI",
    "Natural Conversations",
    "Faster Customer Journeys",
    "Call History and Review",
    "Flexible Workflows",
    "Requests for Team Review",
    "Configured Calling Hours",
    "Consistent Brand Experience",
  ];

  const conversationsIntoAction = [
    { title: "Customer Data", description: "Capture useful information from every conversation." },
    {
      title: "Order Systems",
      description: "No order system connector is available in this release.",
    },
    { title: "Product Information", description: "Give AI access to approved product and business information." },
    {
      title: "Google Calendar",
      description: "Connect it in the dashboard; the assistant there uses it, calls do not.",
    },
    { title: "Requests for Review", description: "Collect important requests for your team to review in your call history." },
    { title: "Follow-Up", description: "Send a follow-up email when Gmail is connected and the campaign allows it." },
  ];

  const howItWorks = [
    {
      title: "Answer",
      description: "The AI handles configured incoming calls within your account and provider limits.",
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
        "Routine conversations can be handled automatically while more complex requests are collected for your team to review.",
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
    "AI Customer Call Support",
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
        "Answer store-related questions, product inquiries, and customer requests even outside regular hours.",
    },
    {
      title: "Multi-Location Retailers",
      description: "Collect the requested location or department as part of a customer request for review.",
    },
    {
      title: "High-Volume Retailers",
      description: "Handle increased call demand during promotions, launches, holidays, and seasonal peaks.",
    },
    {
      title: "Service-Based Retail",
      description: "Collect callback requests, answer customer questions, and run follow-up conversations.",
    },
  ];

  const faqs = [
    {
      question: "What can AI do for retail customer support?",
      answer:
        "AI can answer calls, handle product enquiries, answer order-policy questions, schedule callbacks, and send follow-up emails when set up.",
    },
    {
      question: "Can AI handle e-commerce order inquiries?",
      answer:
        "In general terms. Order lookup is not available; AI answers questions about shipping, delivery and returns policies from your approved information.",
    },
    {
      question: "Can AI answer product questions?",
      answer:
        "Yes. AI can provide answers based on your approved product catalog and business information, including supported questions about features, pricing, sizing, and products.",
    },
    {
      question: "Can AI make outbound retail calls?",
      answer:
        "Yes. Depending on your workflow, AI can support outbound conversations such as customer follow-ups and other approved use cases.",
    },
    {
      question: "Can AI schedule callbacks?",
      answer:
        "Yes. The agent can schedule a callback when callbacks are among the campaign’s approved actions. It does not book into a calendar during a call.",
    },
    {
      question: "Can AI transfer customers to human agents?",
      answer:
        "Yes. When a transfer is set up for the campaign, the agent can transfer a live call to a person on your list. Otherwise it can collect a request for your team to review; this does not arrange a callback.",
    },
    {
      question: "Does AI work after business hours?",
      answer:
        "Yes, within the calling hours you configure, so customers can reach your business outside normal operating hours.",
    },
    {
      question: "Is voice the only channel?",
      answer:
        "Yes. Talk-Lee AI answers and makes phone calls only; it does not handle written messages, and email goes out only as a follow-up.",
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
            inquiries, and support conversations within your calling hours.
          </p>
          <div className={centeredCtaPairClassName}>
            <Button asChild size="lg" className={primaryButtonClassName}>
              <Link href="/auth/register">
                Get Started
              </Link>
            </Button>
            <Button asChild size="lg" variant="outline" className={outlineButtonClassName}>
              <Link href="/book-a-demo">
                Book a Demo
              </Link>
            </Button>
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
            AI can handle configured routine enquiries and collect requests for your team to review.
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
            reason to leave and shop elsewhere. Talk-Lee AI can use approved information to answer routine questions
            and collect requests for review.
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
          <p className={eyebrowClassName}>Configured Calling Hours</p>
          <h2 className={`mt-3 ${headingClassName}`}>Keep Serving Customers Within Your Hours</h2>
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
            can improve. Talk-Lee AI captures valuable details and shows them in your call history.
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
            Talk-Lee AI uses approved product data, logs calls to HubSpot or Salesforce once you connect one, and sends
            follow-up emails when Gmail is connected.
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
            Talk-Lee AI uses approved answers and enabled actions, and captures requests for your team to review.
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
            <Button asChild size="lg" className={primaryButtonClassName}>
              <Link href="/auth/register">
                Start Automating Customer Calls
              </Link>
            </Button>
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
            <h2 className={headingClassName}>Your Next Customer Could Be Calling</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Don&rsquo;t let unanswered calls become lost sales or frustrated customers. Let Talk-Lee AI answer, assist,
              qualify, and keep customer conversations moving.
            </p>
            <div className={centeredCtaPairClassName}>
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
          </div>
        </section>
      </div>
      <Footer />
    </main>
  );
}
