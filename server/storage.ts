import { 
  type User, 
  type InsertUser, 
  type ChatMessage, 
  type InsertChatMessage,
  type SimulationResult,
  type Bet,
  type BettingData,
  type FormGuideData
} from "@shared/schema";
import { randomUUID } from "crypto";

export interface IStorage {
  getUser(id: string): Promise<User | undefined>;
  getUserByUsername(username: string): Promise<User | undefined>;
  createUser(user: InsertUser): Promise<User>;
  createChatMessage(message: InsertChatMessage): Promise<ChatMessage>;
  getChatMessages(): Promise<ChatMessage[]>;
  runSimulation(track: string, race: string, iterations: number): Promise<SimulationResult>;
  getBets(): Promise<BettingData>;
  getFormGuide(): Promise<FormGuideData>;
  getChatResponse(message: string): Promise<string>;
}

const horseNames = [
  "Thunderbolt Express",
  "Midnight Dancer",
  "Golden Phoenix",
  "Storm Chaser",
  "Royal Flush",
  "Shadow Runner",
  "Lucky Strike",
  "Wild Spirit",
  "Desert Wind",
  "Silver Arrow",
];

const trainers = ["Gai Waterhouse", "Chris Waller", "Ciaron Maher", "Peter Moody", "John O'Shea"];
const jockeys = ["James McDonald", "Damien Oliver", "Hugh Bowman", "Kerrin McEvoy", "Glen Boss"];

export class MemStorage implements IStorage {
  private users: Map<string, User>;
  private chatMessages: ChatMessage[];

  constructor() {
    this.users = new Map();
    this.chatMessages = [];
  }

  async getUser(id: string): Promise<User | undefined> {
    return this.users.get(id);
  }

  async getUserByUsername(username: string): Promise<User | undefined> {
    return Array.from(this.users.values()).find(
      (user) => user.username === username,
    );
  }

  async createUser(insertUser: InsertUser): Promise<User> {
    const id = randomUUID();
    const user: User = { ...insertUser, id };
    this.users.set(id, user);
    return user;
  }

  async createChatMessage(message: InsertChatMessage): Promise<ChatMessage> {
    const chatMessage: ChatMessage = {
      id: randomUUID(),
      role: message.role,
      content: message.content,
      createdAt: new Date(),
    };
    this.chatMessages.push(chatMessage);
    return chatMessage;
  }

  async getChatMessages(): Promise<ChatMessage[]> {
    return this.chatMessages;
  }

  async getChatResponse(message: string): Promise<string> {
    const lowerMessage = message.toLowerCase();
    
    if (lowerMessage.includes("algorithm") || lowerMessage.includes("edge")) {
      return "Our edge calculation uses a multi-factor model that combines:\n\n1. **Historical Performance** - Win rate, place rate, and average beaten margins\n2. **Track Suitability** - Performance at specific tracks and conditions\n3. **Distance Analysis** - Optimal distance ranges based on race history\n4. **Class Ratings** - Adjusted performance based on race class levels\n5. **Form Cycle** - Recent form trends and fitness indicators\n\nThe model generates a probability for each horse, which we compare against market odds to calculate Expected Value (EV). A positive EV indicates a value bet opportunity.";
    }
    
    if (lowerMessage.includes("flemington") || lowerMessage.includes("track")) {
      return "**Flemington Analysis:**\n\nFlemington is Australia's premier racecourse, known for:\n\n- **Straight Six** - Unique 1000m straight track favoring on-pace runners\n- **Long Home Straight** - 450m straight rewards horses with strong finishes\n- **Track Bias** - Typically fair, but can favor on-speed in wet conditions\n- **Key Factors** - Barrier draw crucial in sprints, less important over 2000m+\n\nOur model adjusts for Flemington's unique characteristics, particularly the long straight that allows late-finishing horses to run down leaders.";
    }
    
    if (lowerMessage.includes("condition") || lowerMessage.includes("wet") || lowerMessage.includes("rain")) {
      return "**Track Condition Impact:**\n\n- **Good (1-4)** - Standard ratings apply\n- **Soft (5-6)** - Adjust for wet track form, heavier weights disadvantaged\n- **Heavy (7-10)** - Significant form reversals common, specialists thrive\n\nOur model incorporates:\n- Historical wet track performance ratings\n- Weight adjustments based on going\n- Stride length and action analysis\n- Breeding factors (sires known for wet/dry specialists)\n\nWe recommend reducing stake sizes in heavy conditions due to increased volatility.";
    }
    
    if (lowerMessage.includes("feature") || lowerMessage.includes("analyze") || lowerMessage.includes("race")) {
      return "**Today's Feature Race Analysis:**\n\n🏇 **Flemington Race 7 - Group 1 Stakes (1600m)**\n\n**Top Rated:**\n1. **Thunderbolt Express** (92) - Outstanding fresh form, suited to distance\n2. **Golden Phoenix** (89) - Proven at level, drawn ideally\n3. **Storm Chaser** (86) - Distance query but class factor\n\n**Value Play:** Storm Chaser at $8.00 (Model: $5.50)\n- 18.2% win probability vs 12.5% market implied\n- EV: +14.5%\n- Kelly Stake: 2.8% of bankroll\n\n**Risks:** Watch for late market moves on scratchings.";
    }
    
    if (lowerMessage.includes("kelly") || lowerMessage.includes("stake") || lowerMessage.includes("bankroll")) {
      return "**Kelly Criterion Staking:**\n\nWe use a fractional Kelly (usually 25-50%) to manage variance:\n\n**Formula:** f* = (bp - q) / b\n- b = decimal odds - 1\n- p = your estimated probability\n- q = 1 - p\n\n**Example:**\n- Horse at $4.00 (b = 3)\n- Model probability: 30% (p = 0.30)\n- q = 0.70\n- Full Kelly: (3 × 0.30 - 0.70) / 3 = 6.67%\n- Quarter Kelly: 1.67%\n\nWe recommend quarter Kelly for beginners and half Kelly for confident bettors to smooth variance.";
    }
    
    return "I can help you with:\n\n- **Algorithm insights** - Explain how we calculate edges and probabilities\n- **Track analysis** - Discuss specific track characteristics (try asking about Flemington)\n- **Track conditions** - How weather affects form\n- **Race analysis** - Break down today's feature races\n- **Staking strategy** - Kelly Criterion and bankroll management\n\nWhat would you like to explore?";
  }

  async runSimulation(track: string, race: string, iterations: number): Promise<SimulationResult> {
    const numHorses = Math.floor(Math.random() * 4) + 8;
    const selectedHorses = horseNames.slice(0, numHorses);
    
    const rawProbabilities = selectedHorses.map(() => Math.random() * 100);
    const totalProb = rawProbabilities.reduce((a, b) => a + b, 0);
    const normalizedProbs = rawProbabilities.map(p => (p / totalProb) * 100);
    
    normalizedProbs.sort((a, b) => b - a);
    
    const horses = selectedHorses.map((name, i) => {
      const winPercentage = normalizedProbs[i];
      const placePercentage = Math.min(winPercentage * 2.5, 95);
      const impliedOdds = 100 / winPercentage;
      
      let valueRating: "high" | "medium" | "low";
      if (winPercentage > 18) valueRating = "high";
      else if (winPercentage > 10) valueRating = "medium";
      else valueRating = "low";
      
      return {
        name,
        winPercentage,
        placePercentage,
        impliedOdds,
        valueRating,
      };
    });
    
    const convergenceData = Array.from({ length: 10 }, (_, i) => ({
      iteration: ((i + 1) * iterations) / 10,
      variance: Math.max(0.5, 5 - (i * 0.4) + (Math.random() * 0.5)),
    }));
    
    return { horses, convergenceData };
  }

  async getBets(): Promise<BettingData> {
    const tracks = ["Flemington", "Randwick", "Caulfield", "Moonee Valley"];
    const bets: Bet[] = [];
    
    for (let i = 0; i < 8; i++) {
      const modelProb = 10 + Math.random() * 25;
      const marketOdds = (100 / modelProb) * (0.8 + Math.random() * 0.4);
      const marketImpliedProb = 100 / marketOdds;
      const ev = ((modelProb / marketImpliedProb) - 1) * 100;
      const kellyStake = Math.max(0, ((marketOdds - 1) * (modelProb / 100) - (1 - modelProb / 100)) / (marketOdds - 1) * 100);
      
      let confidence: "high" | "medium" | "low";
      if (ev > 15) confidence = "high";
      else if (ev > 8) confidence = "medium";
      else confidence = "low";
      
      bets.push({
        id: randomUUID(),
        track: tracks[Math.floor(Math.random() * tracks.length)],
        raceNumber: Math.floor(Math.random() * 9) + 1,
        horseName: horseNames[Math.floor(Math.random() * horseNames.length)],
        modelProbability: modelProb,
        marketOdds,
        expectedValue: ev,
        kellyStake: Math.min(kellyStake, 5),
        confidence,
        winPercentage: modelProb,
        placePercentage: Math.min(modelProb * 2.5, 95),
        ciLower: modelProb - 5,
        ciUpper: modelProb + 5,
        expectedPosition: Math.floor(Math.random() * 4) + 1,
        positionStdDev: 1.5,
        stabilityScore: 50 + Math.random() * 30,
        runningStyle: 'on_pace',
        paceSplits: { slow: 0, even: 50, fast: 50, melt: 0 },
        edge: ev * 0.5,
        valueRating: confidence === 'high' ? 'high' : confidence === 'medium' ? 'medium' : 'low',
        hasMarketOdds: true,
      });
    }
    
    bets.sort((a, b) => b.expectedValue - a.expectedValue);
    
    const totalEV = bets.reduce((sum, b) => sum + b.expectedValue, 0);
    const totalStake = bets.reduce((sum, b) => sum + b.kellyStake, 0);
    
    return {
      bets,
      summary: {
        totalBets: bets.length,
        averageEV: totalEV / bets.length,
        recommendedAllocation: Math.min(totalStake, 25),
      },
    };
  }

  async getFormGuide(): Promise<FormGuideData> {
    const horses = horseNames.slice(0, 10).map((name, i) => ({
      id: randomUUID(),
      name,
      trainer: trainers[i % trainers.length],
      jockey: jockeys[i % jockeys.length],
      weight: 54 + Math.floor(Math.random() * 6),
      barrier: i + 1,
      form: Array.from({ length: 5 }, () => 
        Math.random() > 0.3 ? Math.floor(Math.random() * 6) + 1 : "x"
      ).join(""),
      rating: 70 + Math.floor(Math.random() * 25),
      trend: (["up", "down", "stable"] as const)[Math.floor(Math.random() * 3)],
    }));
    
    horses.sort((a, b) => b.rating - a.rating);
    
    return {
      track: "Flemington",
      raceNumber: 7,
      raceName: "Australian Cup",
      distance: 2000,
      horses,
    };
  }
}

export const storage = new MemStorage();
