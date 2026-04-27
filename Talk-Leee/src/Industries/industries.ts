import { Briefcase, Building2, GraduationCap, HeartPulse, Landmark, Laptop, Luggage, Megaphone, ShoppingCart, UserCheck } from "lucide-react";

export const industryNavItems = [
  {
    label: "Healthcare",
    href: "/industries/healthcare",
    description: "Automate Patient Calls. Improve Outcomes. Stay Compliant.",
    icon: HeartPulse,
  },
  {
    label: "Recruitment",
    href: "/industries/recruitment",
    description: "Streamline Hiring, Screen Candidates, and Schedule Interviews Seamlessly",
    icon: UserCheck,
  },
  {
    label: "Marketing Automation",
    href: "/industries/marketing-automation",
    description: "Turn Leads into Customers with Smarter AI Marketing Automation",
    icon: Megaphone,
  },
  {
    label: "Financial Services",
    href: "/industries/financial-services",
    description: "Transform Your Financial Operations with Intelligent AI",
    icon: Landmark,
  },
  {
    label: "Travel Industry",
    href: "/industries/travel-industry",
    description: "Intelligent Communication Built for Modern Travel & Hospitality",
    icon: Luggage,
  },
  {
    label: "Retail & E-commerce",
    href: "/industries/retail-ecommerce",
    description: "Smarter Conversations. Faster Sales. Better Customer Experience.",
    icon: ShoppingCart,
  },
  {
    label: "Real Estate",
    href: "/industries/real-estate",
    description: "Automate Communication, Nurture Leads & Close Deals Faster with AI",
    icon: Building2,
  },
  {
    label: "Professional Services",
    href: "/industries/professional-services",
    description: "Smarter Communication & Seamless Client Management for Modern Firms",
    icon: Briefcase,
  },
  {
    label: "Software & Tech Support",
    href: "/industries/software-tech-support",
    description: "Deliver Fast, Reliable & Personalized Tech Support with AI",
    icon: Laptop,
  },
  {
    label: "Education",
    href: "/industries/education",
    description: "Boost Engagement & Streamline School Operations with AI Automation",
    icon: GraduationCap,
  },
] as const;
