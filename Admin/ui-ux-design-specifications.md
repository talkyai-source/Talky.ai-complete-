# Admin Panel UI/UX Design Specifications

## Design Philosophy

### Core Principles
- **Clarity First**: Every interface element should be immediately understandable
- **Efficiency**: Minimize clicks and cognitive load for common tasks
- **Consistency**: Unified design language across all admin functions
- **Accessibility**: WCAG 2.1 AA compliance for inclusive design
- **Responsiveness**: Seamless experience across all device sizes

### Visual Design Language
- **Color Palette**: Professional, accessible color scheme
- **Typography**: Clear hierarchy with system fonts for performance
- **Spacing**: Consistent 8px grid system
- **Shadows**: Subtle elevation for depth and hierarchy
- **Animations**: Purposeful micro-interactions for feedback

## Layout Architecture

### Overall Structure
```
┌─────────────────────────────────────────────────────────────┐
│                        Header Bar                           │
├─────────────┬───────────────────────────────────────────────┤
│             │                                               │
│   Sidebar   │              Main Content Area                │
│ Navigation  │                                               │
│             │                                               │
│             │                                               │
│             │                                               │
│             │                                               │
└─────────────┴───────────────────────────────────────────────┘
```

### Header Bar Components
- **Logo**: Talky.ai brand identity (clickable, returns to dashboard)
- **Search Bar**: Global search with intelligent suggestions
- **Notification Bell**: Real-time alerts with badge counter
- **User Menu**: Profile, settings, logout dropdown
- **Quick Actions**: Frequently used admin functions

### Sidebar Navigation
```
Dashboard (Icon: Home)
├── Overview
├── Analytics
└── Reports

Tenant Management (Icon: Building)
├── All Tenants
├── Add Tenant
└── Suspended Tenants

User Management (Icon: Users)
├── All Users
├── Admin Users
└── Suspended Users

System Health (Icon: Heartbeat)
├── Provider Status
├── Performance Metrics
└── Error Logs

Configuration (Icon: Settings)
├── Providers
├── Features
└── Limits

Security (Icon: Shield)
├── Audit Log
├── Security Events
└── Access Control

Support (Icon: Lifebuoy)
├── System Logs
├── Maintenance
└── Impersonation
```

## Page-by-Page Design Specifications

### 1. Admin Dashboard (`/admin/dashboard`)

#### Layout Structure
```
┌─────────────────────────────────────────────────────────────┐
│  Dashboard Overview                    [Customize Layout]  │
├─────────────────────────────────────────────────────────────┤
│ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌────────┐ │
│ │ Active      │ │ Total       │ │ System      │ │ Revenue│ │
│ │ Tenants     │ │ Users       │ │ Health      │ │ (MTD)  │ │
│ │ 1,247       │ │ 8,932       │ │ 99.9%       │ │ $45.2K │ │
│ │ ↑ 12%       │ │ ↑ 8%        │ │ ↑ 0.1%      │ │ ↑ 15%  │ │
│ └─────────────┘ └─────────────┘ └─────────────┘ └────────┘ │
├─────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────┐ ┌─────────────────────────────┐ │
│ │   Tenant Growth Chart   │ │    Provider Health Status   │ │
│ │   [Line Chart]          │ │   [Status Grid]             │ │
│ │                         │ │   Deepgram: ✅ Healthy      │ │
│ │                         │ │   Cartesia: ✅ Healthy      │ │
│ │                         │ │   Groq: ⚠️ Degraded         │ │
│ │                         │ │   Vonage: ✅ Healthy        │ │
│ └─────────────────────────┘ └─────────────────────────────┘ │
├─────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ Recent Activity                                          │ │
│ │ ┌───────────────────────────────────────────────────────┐ │ │
│ │ │ Time      │ User      │ Action      │ Status │ Details│ │ │
│ │ ├───────────┼───────────┼─────────────┼────────┼────────┤ │ │
│ │ │ 2 min ago │ john@adm..│ Tenant Susp │ Success│ View   │ │ │
│ │ │ 5 min ago │ sarah@adm..│ User Created│ Success│ View   │ │ │
│ │ │ 8 min ago │ mike@adm.. │ Config Chg  │ Success│ View   │ │ │
│ │ └───────────────────────────────────────────────────────┘ │ │
│ └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

#### Component Specifications

**KPI Cards (4-column grid)**
- Background: White with subtle border
- Title: 14px, medium weight, gray-600
- Value: 32px, bold, gray-900
- Change indicator: 12px, green/red with arrow
- Hover effect: Subtle shadow elevation

**Charts Section**
- Chart container: White background, rounded corners
- Title: 18px, semibold, gray-900
- Time range selector: Dropdown in header
- Interactive tooltips on hover
- Responsive height (300px min, 400px max)

**Activity Table**
- Alternating row backgrounds
- Status badges with colors
- Action buttons on hover
- Pagination (25 items default)
- Sortable columns

### 2. Tenant Management (`/admin/tenants`)

#### List View Layout
```
┌─────────────────────────────────────────────────────────────┐
│  Tenant Management                    [Add Tenant] [Export] │
├─────────────────────────────────────────────────────────────┤
│ [Search...] [Status: ▼] [Plan: ▼] [Sort: ▼] [Filter]     │
├─────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ □ Business Name    │ Plan    │ Users │ Calls │ Status │ │ │
│ ├─────────────────────────────────────────────────────────┤ │
│ │ □ Acme Corp        │ Pro     │ 12    │ 1,247 │ Active │ │ │
│ │ □ TechStart Inc    │ Basic   │ 3     │ 156   │ Active │ │ │
│ │ □ Global Solutions │ Ent     │ 45    │ 8,932 │ Susp.  │ │ │
│ │ □ StartupXYZ       │ Pro     │ 8     │ 892   │ Active │ │ │
│ └─────────────────────────────────────────────────────────┘ │ │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ ← Previous │ 1 2 3 4 5 │ Next → │ Page 1 of 23 │        │ │
│ └─────────────────────────────────────────────────────────┘ │ │
└─────────────────────────────────────────────────────────────┘
```

#### Tenant Detail Modal
```
┌─────────────────────────────────────────────────────────────┐
│  Acme Corporation                              [X] Close   │
├─────────────────────────────────────────────────────────────┤
│ ┌──────────────────┐ ┌──────────────────────────────────┐ │
│ │  Tenant Info     │ │  Usage Analytics                 │ │
│ │                  │ │  ┌──────────────────────────────┐ │ │
│ │  Plan: Pro       │ │  │ Minutes: 2,347/5,000 (47%)  │ │ │
│ │  Status: Active  │ │  │ ████████░░░░░░░░░░░░░░░░░░ │ │ │
│ │  Created: Mar 15 │ │  │                              │ │ │
│ │  Users: 12       │ │  │ Calls: 1,247 this month     │ │ │
│ │  Campaigns: 3    │ │  │ Cost: $234.50               │ │ │
│ │                  │ │  └──────────────────────────────┘ │ │
│ └──────────────────┘ └──────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ Recent Activity                                          │ │
│ │ [View full audit log →]                                  │ │
│ └─────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────┤
│ [Edit Details] [Update Quota] [Suspend Tenant] [Delete]    │
└─────────────────────────────────────────────────────────────┘
```

### 3. User Management (`/admin/users`)

#### User List with Advanced Filters
```
┌─────────────────────────────────────────────────────────────┐
│  User Management                    [Add User] [Export]    │
├─────────────────────────────────────────────────────────────┤
│ [Search...] [Role: ▼] [Status: ▼] [Tenant: ▼] [2FA: ▼]    │
├─────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ User              │ Tenant         │ Role │ Status│2FA │ │ │
│ ├─────────────────────────────────────────────────────────┤ │
│ │ john@example.com  │ Acme Corp      │ User │ Active│ ✓  │ │ │
│ │ sarah@example.com │ TechStart Inc  │ Admin│ Active│ ✓  │ │ │
│ │ mike@example.com  │ Global Sol     │ User │ Susp. │ ✗  │ │ │
│ │ jane@example.com  │ StartupXYZ     │ User │ Active│ ✓  │ │ │
│ └─────────────────────────────────────────────────────────┘ │ │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ Bulk Actions: [Suspend] [Delete] [Export] [→]          │ │
│ └─────────────────────────────────────────────────────────┘ │ │
└─────────────────────────────────────────────────────────────┘
```

### 4. System Health Dashboard (`/admin/system-health`)

#### Provider Status Grid
```
┌─────────────────────────────────────────────────────────────┐
│  System Health & Monitoring                                │
├─────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ Provider Status (Last Updated: 2 min ago) [↻ Refresh]   │ │
│ ├─────────────────────────────────────────────────────────┤ │
│ │ Service    │ Provider   │ Status    │ Latency │ Errors │ │ │
│ │ ├──────────┼────────────┼───────────┼─────────┼────────┤ │ │
│ │ STT        │ Deepgram   │ ✅ Healthy│ 145ms   │ 0.1%   │ │ │
│ │ TTS        │ Cartesia   │ ✅ Healthy│ 89ms    │ 0.0%   │ │ │
│ │ LLM        │ Groq       │ ⚠️ Degraded│ 234ms  │ 2.3%   │ │ │
│ │ Telephony  │ Vonage     │ ✅ Healthy│ 56ms    │ 0.2%   │ │ │
│ │ Storage    │ Supabase   │ ✅ Healthy│ 12ms    │ 0.0%   │ │ │
│ └─────────────────────────────────────────────────────────┘ │ │
├─────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────┐ ┌─────────────────────────────┐ │
│ │ System Metrics          │ │  Active Alerts             │ │
│ │                         │ │  ┌───────────────────────┐  │ │
│ │ CPU: 45% ████░░░░░░░░   │ │  │ ⚠️ High Error Rate    │  │ │
│ │ Memory: 62% ██████░░░░  │ │  │ Groq LLM Provider     │  │ │
│ │ Disk: 78% ████████░░░   │ │  │ Started: 15 min ago   │  │ │
│ │ Network: 23% ██░░░░░░░  │ │  │ [Acknowledge] [View]  │  │ │
│ │                         │ │  └───────────────────────┘  │ │
│ │ [View Details →]        │ │  ┌───────────────────────┐  │ │
│ └─────────────────────────┘ │  │ ℹ️ Maintenance Window │  │ │
│                             │  │ Scheduled: Tomorrow   │  │ │
│ ┌─────────────────────────┐ │  │ 02:00-04:00 UTC       │  │ │
│ │ Performance Trends      │ │  │ [Acknowledge] [View]  │  │ │
│ │ [Line Chart]            │ │  └───────────────────────┘  │ │
│ │                         │ │                              │ │
│ └─────────────────────────┘ └──────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### 5. Configuration Panel (`/admin/configuration`)

#### Provider Configuration
```
┌─────────────────────────────────────────────────────────────┐
│  System Configuration                                      │
├─────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ Speech-to-Text Providers                                 │ │
│ │ ┌───────────────────────────────────────────────────────┐ │ │
│ │ │ Active: Deepgram ▼                                    │ │ │
│ │ │ API Key: •••••••••• [Test] [Edit]                   │ │ │
│ │ │ Model: flux-general-en [Edit]                         │ │ │
│ │ │ Status: ✅ Operational [View Logs]                    │ │ │
│ │ │                                                       │ │ │
│ │ │ Available Providers:                                  │ │ │
│ │ │ • Deepgram (Active) ✅                               │ │ │
│ │ │ • Whisper ⚪                                          │ │ │
│ │ │ • Google Cloud STT ⚪                                 │ │ │
│ │ └───────────────────────────────────────────────────────┘ │ │
│ │ [Save Changes] [Reset to Defaults]                      │ │ │
│ └─────────────────────────────────────────────────────────┘ │ │
├─────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ Text-to-Speech Providers                                 │ │
│ │ ┌───────────────────────────────────────────────────────┐ │ │
│ │ │ Active: Cartesia ▼                                    │ │ │
│ │ │ API Key: •••••••••• [Test] [Edit]                   │ │ │
│ │ │ Model: sonic-3 [Edit]                                 │ │ │
│ │ │ Status: ✅ Operational [View Logs]                    │ │ │
│ │ └───────────────────────────────────────────────────────┘ │ │
│ │ [Save Changes] [Reset to Defaults]                      │ │ │
│ └─────────────────────────────────────────────────────────┘ │ │
└─────────────────────────────────────────────────────────────┘
```

## Component Design System

### Buttons

**Primary Button**
- Background: Blue-600 (#2563eb)
- Text: White, 14px, medium weight
- Padding: 8px 16px
- Border radius: 6px
- Hover: Blue-700 with shadow
- Active: Blue-800
- Disabled: Gray-300 with gray-500 text

**Secondary Button**
- Background: White
- Border: 1px solid Gray-300
- Text: Gray-700, 14px, medium weight
- Padding: 8px 16px
- Border radius: 6px
- Hover: Gray-50 background

**Danger Button**
- Background: Red-600 (#dc2626)
- Text: White, 14px, medium weight
- Used for destructive actions
- Hover: Red-700

### Form Elements

**Input Fields**
- Border: 1px solid Gray-300
- Border radius: 6px
- Padding: 8px 12px
- Font: 14px, regular
- Focus: Blue-500 border with shadow
- Error: Red-500 border with red text

**Select Dropdowns**
- Same styling as inputs
- Custom arrow icon
- Maximum height: 200px with scroll
- Search functionality for long lists

**Toggle Switches**
- Size: 44px × 24px
- Active: Blue-600 background
- Inactive: Gray-300 background
- Smooth transition animation

### Data Tables

**Table Structure**
- Header: Gray-100 background, semibold text
- Rows: Alternating white and gray-50
- Hover: Light blue background
- Border: 1px solid Gray-200
- Padding: 12px vertical, 16px horizontal

**Interactive Elements**
- Sortable columns with arrow indicators
- Row selection with checkboxes
- Bulk actions toolbar
- Pagination controls
- Column visibility toggles

### Status Badges

**Success Badge**
- Background: Green-100
- Text: Green-800
- Border: 1px solid Green-200
- Icon: Checkmark

**Warning Badge**
- Background: Yellow-100
- Text: Yellow-800
- Border: 1px solid Yellow-200
- Icon: Exclamation triangle

**Error Badge**
- Background: Red-100
- Text: Red-800
- Border: 1px solid Red-200
- Icon: X circle

**Info Badge**
- Background: Blue-100
- Text: Blue-800
- Border: 1px solid Blue-200
- Icon: Information circle

### Modals and Dialogs

**Modal Structure**
- Overlay: Black with 50% opacity
- Container: White background, rounded corners
- Padding: 24px
- Max width: 600px (large), 400px (medium), 300px (small)
- Close button: Top-right corner
- Backdrop click to close (configurable)

**Confirmation Dialogs**
- Clear title and message
- Primary action (danger for destructive)
- Secondary cancel button
- Loading state during processing

## Responsive Design

### Breakpoints
- **Mobile**: 320px - 767px
- **Tablet**: 768px - 1023px
- **Desktop**: 1024px - 1279px
- **Large Desktop**: 1280px+

### Mobile Adaptations
- Collapsible sidebar navigation
- Stacked KPI cards
- Horizontal scrolling tables
- Full-screen modals
- Touch-optimized buttons (minimum 44px)

### Tablet Adaptations
- Condensed sidebar
- 2-column grid for KPIs
- Responsive table columns
- Optimized modal sizes

## Accessibility Features

### Color Contrast
- All text meets WCAG 2.1 AA standards
- Minimum 4.5:1 contrast ratio for normal text
- Minimum 3:1 contrast ratio for large text
- Color-blind friendly status indicators

### Keyboard Navigation
- Full keyboard accessibility
- Tab order follows visual hierarchy
- Skip links for screen readers
- Keyboard shortcuts for common actions

### Screen Reader Support
- Semantic HTML structure
- ARIA labels and descriptions
- Live regions for dynamic content
- Alternative text for icons and images

### Focus Management
- Visible focus indicators
- Focus trapping in modals
- Logical focus flow
- Focus restoration after interactions

## Animation and Transitions

### Page Transitions
- Fade in/out: 200ms ease-in-out
- Slide transitions: 300ms ease-out
- No animation on initial load for performance

### Micro-interactions
- Button hover: 150ms transition
- Form field focus: 200ms transition
- Loading states: Smooth progress indicators
- Success/error feedback: Brief animations

### Loading States
- Skeleton screens for content loading
- Spinner animations for actions
- Progress bars for long operations
- Staggered loading for lists

## Dark Mode Support

### Color Adaptations
- Background: Gray-900 (#111827)
- Surface: Gray-800 (#1f2937)
- Text: Gray-100 (#f3f4f6)
- Borders: Gray-700 (#374151)
- Primary: Blue-500 (#3b82f6)

### Component Adjustments
- Reduced shadow intensity
- Adjusted contrast ratios
- Inverted icon colors
- Modified hover states

## Performance Considerations

### Loading Optimization
- Lazy loading for images and components
- Code splitting by route
- Progressive enhancement
- Optimistic UI updates

### Interaction Performance
- Debounced search inputs
- Throttled scroll events
- Virtual scrolling for large lists
- Memoized components

### Asset Optimization
- SVG icons for scalability
- Web fonts with fallbacks
- Compressed images
- Efficient CSS delivery

This comprehensive UI/UX design specification ensures a consistent, accessible, and user-friendly admin panel that meets modern design standards while providing efficient administrative functionality.