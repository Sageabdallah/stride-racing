# Horse Racing Analytics Platform - Design Guidelines

## Design Approach: Reference-Based (McLaren F1 Website)
Premium motorsport aesthetic applied to thoroughbred racing - sleek, data-driven, professional with high-impact visuals.

## Core Design Elements

### Color System
```
Primary Background: #000000 to #0a0a0a (deep black)
Accent Orange: #FF8000 (McLaren papaya) - primary CTAs
Accent Gold: #D4AF37 - premium touches
Text Primary: #FFFFFF
Text Secondary: #888888
Text Muted: #555555
Card Backgrounds: #111111
Success: #00C853
Warning: #FFD600
Danger: #FF1744
Border: #222222
```

### Typography
- **Font Family:** Inter or Helvetica Neue
- **Navigation/Buttons:** UPPERCASE, letter-spacing: 2-3px, bold
- **Headlines:** Bold, large (text-5xl to text-7xl), impactful, tracking-wider
- **Body Text:** Clean, readable, subtle

### Layout System
**Spacing:** Use Tailwind units of 4, 8, 16, 20 for consistency (p-4, gap-8, mb-16, bottom-20)

## Component Library

### Navigation Bar (Fixed)
- Semi-transparent black background
- Left: Hamburger menu icon
- Center: "ANALYTICS" | "TRACKS" | "FORM GUIDE" | "ABOUT" (uppercase, white)
- Right: Brand logo "EQUINE EDGE"
- Height: Standard navbar height

### Hero Section (Full Viewport)
- **Background Image:** High-quality horse racing action shot (horses thundering down straight, dramatic finish line, starting gates, or silhouette at dawn)
- **Overlay:** Dark gradient (bottom to top) for text readability
- **Centered Content:**
  - Main headline: "PRECISION RACING" (text-5xl md:text-7xl, bold, tracking-wider)
  - Subtitle: "INTELLIGENCE" (text-2xl md:text-3xl, light, tracking-widest)
  - Tertiary: "AI-Powered Analytics for the Modern Punter"

### Action Buttons (Hero Bottom)
Three equal-width buttons (200px each, 20px gaps) positioned at bottom-20:
- **Background:** Racing orange (#FF8000), blurred backdrop
- **Text:** Black, uppercase, bold, tracking-widest
- **Labels:** "ASK CLAUDE" | "RUN SIMULATIONS" | "FIND BETS"
- **Border-radius:** 0 or minimal (2-4px)
- **Hover:** Lighter orange (#FF9933), subtle glow
- **No manual hover states** - use component defaults

### Page: AI Chat Interface
- Slide-in panel/modal, dark theme (#111111)
- Chat bubbles: User (gray), AI (orange accent border)
- Suggested prompts displayed as clickable cards
- Input field at bottom with send button (orange)

### Page: Simulation Dashboard
- **Input Section:**
  - Race selector dropdown (track, race number, date)
  - Simulations slider (1,000-100,000)
  - "RUN SIMULATION" button (orange, uppercase)
- **Output Section:**
  - Win probability bar chart/histogram
  - Results table: Horse | Win % | Place % | Implied Odds | Value Rating
  - Confidence intervals visualization

### Page: Betting Dashboard
- Card-based layout (#111111 cards)
- **Each Bet Card:**
  - Track & Race info (top, small, #888888)
  - Horse name (large, bold, white)
  - Model probability vs Market odds comparison
  - Expected Value % (color-coded: green/amber/red)
  - Suggested stake (Kelly fraction)
  - Confidence level indicator
- **Filters:** Track, Bet Type, Minimum EV, Confidence Level
- **Summary Stats Bar:** Total Bets | Average EV | Bankroll Allocation

## Animations
- Hero: Subtle Ken Burns effect on background image
- Text: Fade-in on load with stagger
- Buttons: Slide up from bottom with delay
- Page transitions: Smooth fades
- Dashboard cards: Stagger animation on load
- Statistics: Count-up effect when loading
- Loading states: Orange pulse skeleton loaders

## Images
- **Hero Image (Required):** Full-screen, high-quality horse racing action photograph positioned as background. Ensure dramatic composition with horses in motion, preferably finish line or starting gates moment.

## Responsive Behavior
- Mobile: Stack buttons vertically, reduce hero text sizes
- All device sizes: Maintain premium aesthetic and readability