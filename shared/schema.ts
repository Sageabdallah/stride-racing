import { sql } from "drizzle-orm";
import { pgTable, text, varchar, integer, real, timestamp, boolean, jsonb } from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod";

export const users = pgTable("users", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  username: text("username").notNull().unique(),
  password: text("password").notNull(),
});

export const chatMessages = pgTable("chat_messages", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  role: text("role").notNull(),
  content: text("content").notNull(),
  createdAt: timestamp("created_at").defaultNow(),
});

export const chatFeedback = pgTable("chat_feedback", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  sessionId: varchar("session_id").notNull(),
  messageId: varchar("message_id").notNull(),
  rating: integer("rating").notNull(), // 1 = thumbs up, -1 = thumbs down
  promptVersion: varchar("prompt_version", { length: 20 }),
  createdAt: timestamp("created_at").defaultNow(),
});

// Stored races from The Racing API
export const races = pgTable("races", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  raceId: text("race_id"), // External API race ID
  meetId: text("meet_id"),
  track: text("track").notNull(),
  raceNumber: integer("race_number").notNull(),
  raceName: text("race_name"),
  distance: text("distance"),
  raceClass: text("race_class"),
  going: text("going"),
  offTime: timestamp("off_time"),
  raceDate: text("race_date").notNull(),
  status: text("status").default("upcoming"), // upcoming, processing, processed, completed
  runnersJson: jsonb("runners_json"), // Store raw runners data
  createdAt: timestamp("created_at").defaultNow(),
  updatedAt: timestamp("updated_at").defaultNow(),
});

// Processed value bet selections
export const selections = pgTable("selections", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  raceId: varchar("race_id").references(() => races.id),
  track: text("track").notNull(),
  raceNumber: integer("race_number").notNull(),
  raceName: text("race_name"),
  raceDate: text("race_date").notNull(),
  offTime: timestamp("off_time"),
  distance: text("distance"),
  horseName: text("horse_name").notNull(),
  horseNumber: text("horse_number"),
  barrier: text("barrier"),
  jockey: text("jockey"),
  trainer: text("trainer"),
  form: text("form"),
  // Monte Carlo results
  winPercentage: real("win_percentage").notNull(),
  placePercentage: real("place_percentage"),
  modelProbability: real("model_probability").notNull(),
  impliedOdds: real("implied_odds"),
  marketOdds: real("market_odds"),
  expectedValue: real("expected_value"),
  edge: real("edge"),
  kellyStake: real("kelly_stake"),
  ciLower: real("ci_lower"),
  ciUpper: real("ci_upper"),
  expectedPosition: real("expected_position"),
  positionStdDev: real("position_std_dev"),
  stabilityScore: real("stability_score"),
  runningStyle: text("running_style"),
  paceSplitsJson: jsonb("pace_splits_json"),
  confidence: text("confidence").default("low"), // high, medium, low
  valueRating: text("value_rating").default("low"), // high, medium, low
  isActive: boolean("is_active").default(true),
  createdAt: timestamp("created_at").defaultNow(),
  processedAt: timestamp("processed_at").defaultNow(),
  // Enhanced ML features for learning
  mlAdjustment: real("ml_adjustment"),
  groundSuitability: real("ground_suitability"),
  distanceStrikeRate: real("distance_strike_rate"),
  courseStrikeRate: real("course_strike_rate"),
  weightedFormScore: real("weighted_form_score"),
  isFirstUp: boolean("is_first_up").default(false),
  isSecondUp: boolean("is_second_up").default(false),
  daysSinceRun: integer("days_since_run"),
  isCourseDistanceWinner: boolean("is_course_distance_winner").default(false),
  isWinningCombo: boolean("is_winning_combo").default(false),
  jockeyTrainerStrikeRate: real("jockey_trainer_strike_rate"),
  // Market movement analysis
  openingOdds: real("opening_odds"),
  steamDriftPct: real("steam_drift_pct"), // Positive = steamed, negative = drifted
  isSharpMoney: boolean("is_sharp_money").default(false),
  marketMoveCategory: text("market_move_category"), // 'strong_steam', 'steam', 'steady', 'drift', 'strong_drift'
  // Speed rating
  speedRating: real("speed_rating"),
  lastSpeedRating: real("last_speed_rating"),
  bestSpeedRating: real("best_speed_rating"),
  // Pace analysis
  paceScore: real("pace_score"),
  expectedPaceAdvantage: real("expected_pace_advantage"),
  // Enhanced features (class movement, barrier bias, head-to-head)
  enhancedFactor: real("enhanced_factor"),
  classMovementFactor: real("class_movement_factor"),
  classMovementDesc: text("class_movement_desc"),
  barrierBiasFactor: real("barrier_bias_factor"),
  barrierBiasDesc: text("barrier_bias_desc"),
  headToHeadFactor: real("head_to_head_factor"),
  headToHeadDesc: text("head_to_head_desc"),
  enhancedExplanations: text("enhanced_explanations"),
  // Track-specific bias points
  trackBiasPoints: integer("track_bias_points"),
  trackBiasFit: text("track_bias_fit"), // excellent, good, neutral, poor
  trackBiasBarrierPts: integer("track_bias_barrier_pts"),
  trackBiasPacePts: integer("track_bias_pace_pts"),
  trackBiasJockeyPts: integer("track_bias_jockey_pts"),
  trackBiasTrainerPts: integer("track_bias_trainer_pts"),
  trackBiasSummary: text("track_bias_summary"),
  aiInsight: text("ai_insight"),
  aiInsightGeneratedAt: timestamp("ai_insight_generated_at"),
  aiScore: real("ai_score"),
  aiReasoningJson: jsonb("ai_reasoning_json"),
  llmProvider: text("llm_provider"),
  frankingElo: real("franking_elo"),
  frankingScore: real("franking_score"),
  frankingConfidence: real("franking_confidence"),
  isAntiFranked: boolean("is_anti_franked").default(false),
  fieldStrengthAvg: real("field_strength_avg"),
  formQualityTrend: real("form_quality_trend"),
  bestAdjustedMargin: real("best_adjusted_margin"),
  collateralAdvantage: real("collateral_advantage"),
  pagerankAuthority: real("pagerank_authority"),
  communityStrength: real("community_strength"),
  formStability: real("form_stability"),
  graphFrankingScore: real("graph_franking_score"),
  graphFrankingDepth: integer("graph_franking_depth"),
  graphFrankingIndependence: integer("graph_franking_independence"),
  marketValidatedFranking: boolean("market_validated_franking").default(false),
  bridgeScore: real("bridge_score"),
  // Fitness peak indicators
  fitnessRunsThisPrep: integer("fitness_runs_this_prep"),
  fitnessRunLabel: text("fitness_run_label"),
  fitnessIsAtPeakRun: boolean("fitness_is_at_peak_run").default(false),
  fitnessReadinessScore: real("fitness_readiness_score"),
  fitnessPrepTrajectory: text("fitness_prep_trajectory"),
  fitnessDescription: text("fitness_description"),
  fitnessDataJson: jsonb("fitness_data_json"),
  // Recalibration & Sectional MC metadata
  recalibrationApplied: boolean("recalibration_applied").default(false),
  rawWinProb: real("raw_win_prob"),
  calibratedWinProb: real("calibrated_win_prob"),
  recalibrationShift: real("recalibration_shift"),
  sectionalMcEnhanced: boolean("sectional_mc_enhanced").default(false),
  sectionalMcWinProb: real("sectional_mc_win_prob"),
  blendedBaseProb: real("blended_base_prob"),
  paceScenarioJson: jsonb("pace_scenario_json"),
  mlModelActive: boolean("ml_model_active").default(false),
  mlAdjustmentBreakdown: jsonb("ml_adjustment_breakdown"),
  sectionalEdgeMultiplier: real("sectional_edge_multiplier"),
  sectionalEdgeInsight: text("sectional_edge_insight"),
  bankerFlag: boolean("banker_flag").default(false),
  bankerTier: text("banker_tier"),
  bankerScore: real("banker_score"),
  bankerScoreComponents: jsonb("banker_score_components"),
  bankerStaking: jsonb("banker_staking"),
  bankerExoticCombos: jsonb("banker_exotic_combos"),
  bankerQualificationLog: jsonb("banker_qualification_log"),
  selectionType: text("selection_type").default("bestBet"),
  exoticType: text("exotic_type"),
  exoticRationale: text("exotic_rationale"),
  exoticPicksJson: jsonb("exotic_picks_json"),
  fieldSize: integer("field_size"),
  raceCompetitiveness: real("race_competitiveness"),
  closingSpOdds: real("closing_sp_odds"),
  clvRatio: real("clv_ratio"),
  resultPosition: integer("result_position"),
  resultedAt: timestamp("resulted_at"),
  // Luckless last-start analysis
  lucklessFlag: boolean("luckless_flag").default(false),
  lucklessScore: integer("luckless_score"),
  lucklessUplift: real("luckless_uplift"),
  lucklessExplanation: text("luckless_explanation"),
  lucklessJson: jsonb("luckless_json"),
  // Consensus Intelligence Layer
  consensusScore: real("consensus_score"),
  consensusMentionCount: integer("consensus_mention_count"),
  consensusBucketDiversity: integer("consensus_bucket_diversity"),
  marketSignalScore: real("market_signal_score"),
  marketSignalCategory: text("market_signal_category"),
  convergenceScore: real("convergence_score"),
  convergenceTier: text("convergence_tier"), // LOCK | CONFIRM | FLAG | CROWD_OVERRIDE | SKIP
  // Consensus Intelligence V2 — injection + confidence columns
  selectionScoreRaw: real("selection_score_raw"),
  consensusVotePct: real("consensus_vote_pct"),
  consensusInjection: real("consensus_injection"),
  consensusInjectionReason: text("consensus_injection_reason"),
  marketInjection: real("market_injection"),
  marketInjectionReason: text("market_injection_reason"),
  convergenceGate: text("convergence_gate"),
  marketConfidenceScore: real("market_confidence_score"),
  marketConfidenceLabel: text("market_confidence_label"),
  marketConfidenceColour: text("market_confidence_colour"),
  tipstersPolled: integer("tipsters_polled"),
  independentSourceRate: real("independent_source_rate"),
});

// Pipeline run log
export const pipelineRuns = pgTable("pipeline_runs", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  status: text("status").notNull().default("running"), // running, completed, failed
  startedAt: timestamp("started_at").defaultNow(),
  completedAt: timestamp("completed_at"),
  meetsProcessed: integer("meets_processed").default(0),
  racesProcessed: integer("races_processed").default(0),
  selectionsCreated: integer("selections_created").default(0),
  errorMessage: text("error_message"),
  dataSource: text("data_source"), // racing_api, local
});

// Selection results - track actual outcomes for performance analysis
export const selectionResults = pgTable("selection_results", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  selectionId: varchar("selection_id").references(() => selections.id),
  raceId: varchar("race_id"),
  track: text("track").notNull(),
  raceNumber: integer("race_number").notNull(),
  raceDate: text("race_date").notNull(),
  horseName: text("horse_name").notNull(),
  
  // Prediction data (copied from selection at time of bet)
  predictedWinProb: real("predicted_win_prob"),
  predictedPlaceProb: real("predicted_place_prob"),
  marketOdds: real("market_odds"),
  openingOdds: real("opening_odds"),
  confidence: text("confidence"),
  edge: real("edge"),
  
  // Actual outcome
  actualPosition: integer("actual_position"),
  fieldSize: integer("field_size"),
  won: boolean("won").default(false),
  placed: boolean("placed").default(false), // Top 3
  startingPrice: real("starting_price"),
  
  // Profit/loss calculation (level $100 stake)
  stake: real("stake").default(100),
  returnAmount: real("return_amount").default(0),
  profitLoss: real("profit_loss").default(0),
  
  // Market movement data
  steamDriftPct: real("steam_drift_pct"), // Positive = steamed (shortened), negative = drifted
  isSharpMoney: boolean("is_sharp_money").default(false),
  marketMoveCategory: text("market_move_category"), // 'strong_steam', 'steam', 'steady', 'drift', 'strong_drift'
  
  // Metadata
  resultedAt: timestamp("resulted_at").defaultNow(),
  createdAt: timestamp("created_at").defaultNow(),
});

export const strideTipResults = pgTable("stride_tip_results", {
  tipDate: text("tip_date").notNull(),
  track: text("track").notNull(),
  raceNumber: integer("race_number").notNull(),
  raceId: text("race_id"),
  tippedHorseId: text("tipped_horse_id"),
  tippedHorseName: text("tipped_horse_name").notNull(),
  actualWinnerId: text("actual_winner_id"),
  actualWinnerName: text("actual_winner_name"),
  result: text("result").notNull(),
  tippedHorseSp: real("tipped_horse_sp"),
  winnerSp: real("winner_sp"),
  edgePct: real("edge_pct"),
  createdAt: timestamp("created_at").defaultNow(),
});

// Performance tracking - aggregated stats by various dimensions
export const performanceStats = pgTable("performance_stats", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  dimension: text("dimension").notNull(), // 'overall', 'track', 'distance', 'confidence', 'odds_range'
  dimensionValue: text("dimension_value").notNull(), // e.g., 'Randwick', 'sprint', 'high', '$2-5'
  periodStart: text("period_start").notNull(),
  periodEnd: text("period_end").notNull(),
  
  // Stats
  totalBets: integer("total_bets").default(0),
  winners: integer("winners").default(0),
  placers: integer("placers").default(0),
  strikeRate: real("strike_rate").default(0), // winners / total_bets
  placeRate: real("place_rate").default(0),
  roi: real("roi").default(0), // (returns - stakes) / stakes * 100
  profitLoss: real("profit_loss").default(0),
  averageOdds: real("average_odds").default(0),
  averageEdge: real("average_edge").default(0),
  
  // Market movement performance
  steamWinRate: real("steam_win_rate"),
  driftWinRate: real("drift_win_rate"),
  sharpMoneyWinRate: real("sharp_money_win_rate"),
  
  updatedAt: timestamp("updated_at").defaultNow(),
});

// Training data for ML - archived race results with predictions vs outcomes
export const trainingData = pgTable("training_data", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  raceId: text("race_id"), // External API race ID
  track: text("track").notNull(),
  raceNumber: integer("race_number").notNull(),
  raceDate: text("race_date").notNull(),
  distance: text("distance"),
  going: text("going"),
  raceClass: text("race_class"),
  
  // Horse data
  horseName: text("horse_name").notNull(),
  horseNumber: text("horse_number"),
  barrier: integer("barrier"),
  jockey: text("jockey"),
  trainer: text("trainer"),
  weight: real("weight"),
  
  // Prediction data (what our model predicted)
  predictedWinProb: real("predicted_win_prob"),
  predictedPlaceProb: real("predicted_place_prob"),
  predictedPosition: real("predicted_position"),
  confidence: text("confidence"),
  expectedValue: real("expected_value"),
  marketOdds: real("market_odds"),
  
  // Actual outcome
  actualPosition: integer("actual_position"),
  won: integer("won").default(0), // 0 or 1
  placed: integer("placed").default(0), // 0 or 1 (top 3)
  startingPrice: real("starting_price"),
  
  // Metadata
  archivedAt: timestamp("archived_at").defaultNow(),
  selectionId: varchar("selection_id"), // Reference to original selection
});

// Feature snapshots - log full feature vectors during live inference for training-inference gap analysis
export const featureSnapshots = pgTable("feature_snapshots", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  track: text("track").notNull(),
  raceNumber: integer("race_number").notNull(),
  raceDate: text("race_date").notNull(),
  horseName: text("horse_name").notNull(),
  horseNumber: text("horse_number"),
  featuresJson: jsonb("features_json").notNull(),
  featureCount: integer("feature_count"),
  modelPrediction: real("model_prediction"),
  predictionSource: text("prediction_source").default("inference"),
  createdAt: timestamp("created_at").defaultNow(),
});

// Prediction audit - track predicted probability vs actual result for live performance monitoring
export const predictionAudit = pgTable("prediction_audit", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  selectionId: varchar("selection_id"),
  track: text("track").notNull(),
  raceNumber: integer("race_number").notNull(),
  raceDate: text("race_date").notNull(),
  offTime: text("off_time"),
  horseName: text("horse_name").notNull(),
  predictedWinProb: real("predicted_win_prob"),
  predictedPlaceProb: real("predicted_place_prob"),
  marketOdds: real("market_odds"),
  confidence: text("confidence"),
  edge: real("edge"),
  actualPosition: integer("actual_position"),
  fieldSize: integer("field_size"),
  won: boolean("won"),
  placed: boolean("placed"),
  startingPrice: real("starting_price"),
  profitLoss: real("profit_loss"),
  resultCollectedAt: timestamp("result_collected_at"),
  resultStatus: text("result_status").default("pending"),
  createdAt: timestamp("created_at").defaultNow(),
});

// Race schedule for automated results collection
export const raceSchedule = pgTable("race_schedule", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  track: text("track").notNull(),
  raceNumber: integer("race_number").notNull(),
  raceDate: text("race_date").notNull(),
  offTime: timestamp("off_time"),
  resultDueAt: timestamp("result_due_at"),
  resultStatus: text("result_status").default("pending"),
  resultCollectedAt: timestamp("result_collected_at"),
  retryCount: integer("retry_count").default(0),
  createdAt: timestamp("created_at").defaultNow(),
});

export const insertFeatureSnapshotSchema = createInsertSchema(featureSnapshots).omit({
  id: true,
  createdAt: true,
});

export const insertPredictionAuditSchema = createInsertSchema(predictionAudit).omit({
  id: true,
  createdAt: true,
});

export const insertRaceScheduleSchema = createInsertSchema(raceSchedule).omit({
  id: true,
  createdAt: true,
});

// Advanced Race Analysis - Full Field "Train of Thought" via Groq
export const raceAnalyses = pgTable("race_analyses", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  track: text("track").notNull(),
  raceNumber: integer("race_number").notNull(),
  raceDate: text("race_date").notNull(),
  raceName: text("race_name"),
  distance: text("distance"),
  going: text("going"),
  raceClass: text("race_class"),
  fieldSize: integer("field_size"),
  
  // Pace Scenario Analysis
  paceScenario: text("pace_scenario"), // hot_pace, soft_lead, honest_tempo, unknown
  paceDescription: text("pace_description"),
  speedHorsesJson: jsonb("speed_horses_json"),
  stalkersJson: jsonb("stalkers_json"),
  closersJson: jsonb("closers_json"),
  
  // Winner & Places (predictions)
  predictedWinner: text("predicted_winner"),
  predictedWinnerNumber: integer("predicted_winner_number"),
  predictedWinnerProb: real("predicted_winner_prob"),
  predictedWinnerConfidence: real("predicted_winner_confidence"),
  predictedPlacesJson: jsonb("predicted_places_json"),
  
  // Race-Level Analysis
  raceSummary: text("race_summary"),
  keyDynamics: text("key_dynamics"),
  bettingRecommendation: text("betting_recommendation"),
  
  // Full Field Analysis (JSON array of all horses with 4-phase analysis)
  horsesAnalysisJson: jsonb("horses_analysis_json"),
  
  // Metadata
  analysisTimestamp: timestamp("analysis_timestamp").defaultNow(),
  modelUsed: text("model_used"),
  iterations: integer("iterations").default(1),
  isActive: boolean("is_active").default(true),
  createdAt: timestamp("created_at").defaultNow(),
});

// Individual Horse Race Analysis (for querying specific horses)
export const horseRaceAnalyses = pgTable("horse_race_analyses", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  raceAnalysisId: varchar("race_analysis_id").references(() => raceAnalyses.id),
  
  // Race Context
  track: text("track").notNull(),
  raceNumber: integer("race_number").notNull(),
  raceDate: text("race_date").notNull(),
  
  // Horse Identity
  horseName: text("horse_name").notNull(),
  horseNumber: integer("horse_number"),
  barrier: integer("barrier"),
  
  // 4-Phase Analysis
  phase1Profile: text("phase_1_profile"),
  phase2Pace: text("phase_2_pace"),
  phase3Class: text("phase_3_class"),
  phase4Comparative: text("phase_4_comparative"),
  trainOfThought: text("train_of_thought"),
  
  // Assessment
  category: text("category"), // Contender, Place Hope, Lucky to Place, Outclassed
  assessedWinProb: real("assessed_win_prob"),
  assessedPlaceProb: real("assessed_place_prob"),
  predictedFinishPosition: integer("predicted_finish_position"),
  confidencePct: real("confidence_pct"),
  
  // Market Intelligence
  marketOdds: real("market_odds"),
  marketAssessment: text("market_assessment"), // Value, Overbet, Correctly Priced
  valueEdgePct: real("value_edge_pct"),
  
  // Win/Place Logic
  canWin: boolean("can_win").default(false),
  placePotential: boolean("place_potential").default(false),
  cannotWinReason: text("cannot_win_reason"),
  upsetScenario: text("upset_scenario"),
  
  // Comparative Analysis
  comparativeAdvantage: text("comparative_advantage"),
  keyRivalsJson: jsonb("key_rivals_json"),
  beatsWhomJson: jsonb("beats_whom_json"),
  losesToWhomJson: jsonb("loses_to_whom_json"),
  
  createdAt: timestamp("created_at").defaultNow(),
});

export const insertRaceAnalysisSchema = createInsertSchema(raceAnalyses).omit({
  id: true,
  createdAt: true,
  analysisTimestamp: true,
});

export const insertHorseRaceAnalysisSchema = createInsertSchema(horseRaceAnalyses).omit({
  id: true,
  createdAt: true,
});

export type InsertRaceAnalysis = z.infer<typeof insertRaceAnalysisSchema>;
export type RaceAnalysis = typeof raceAnalyses.$inferSelect;
export type InsertHorseRaceAnalysis = z.infer<typeof insertHorseRaceAnalysisSchema>;
export type HorseRaceAnalysis = typeof horseRaceAnalyses.$inferSelect;

export const insertUserSchema = createInsertSchema(users).pick({
  username: true,
  password: true,
});

export const insertChatMessageSchema = createInsertSchema(chatMessages).pick({
  role: true,
  content: true,
});

export const insertRaceSchema = createInsertSchema(races).omit({
  id: true,
  createdAt: true,
  updatedAt: true,
});

export const insertSelectionSchema = createInsertSchema(selections).omit({
  id: true,
  createdAt: true,
  processedAt: true,
});

export const insertPipelineRunSchema = createInsertSchema(pipelineRuns).omit({
  id: true,
  startedAt: true,
});

export const insertTrainingDataSchema = createInsertSchema(trainingData).omit({
  id: true,
  archivedAt: true,
});

export const insertSelectionResultSchema = createInsertSchema(selectionResults).omit({
  id: true,
  createdAt: true,
});

export const insertStrideTipResultSchema = createInsertSchema(strideTipResults).omit({
  createdAt: true,
});

export const insertPerformanceStatsSchema = createInsertSchema(performanceStats).omit({
  id: true,
  updatedAt: true,
});

export type InsertUser = z.infer<typeof insertUserSchema>;
export type User = typeof users.$inferSelect;
export type InsertChatMessage = z.infer<typeof insertChatMessageSchema>;
export type ChatMessage = typeof chatMessages.$inferSelect;
export type InsertRace = z.infer<typeof insertRaceSchema>;
export type Race = typeof races.$inferSelect;
export type InsertSelection = z.infer<typeof insertSelectionSchema>;
export type Selection = typeof selections.$inferSelect;
export type InsertPipelineRun = z.infer<typeof insertPipelineRunSchema>;
export type PipelineRun = typeof pipelineRuns.$inferSelect;
export type InsertTrainingData = z.infer<typeof insertTrainingDataSchema>;
export type TrainingData = typeof trainingData.$inferSelect;
export type InsertSelectionResult = z.infer<typeof insertSelectionResultSchema>;
export type SelectionResult = typeof selectionResults.$inferSelect;
export type InsertStrideTipResult = z.infer<typeof insertStrideTipResultSchema>;
export type StrideTipResult = typeof strideTipResults.$inferSelect;
export type InsertPerformanceStats = z.infer<typeof insertPerformanceStatsSchema>;
export type PerformanceStats = typeof performanceStats.$inferSelect;
export type InsertFeatureSnapshot = z.infer<typeof insertFeatureSnapshotSchema>;
export type FeatureSnapshot = typeof featureSnapshots.$inferSelect;
export type InsertPredictionAudit = z.infer<typeof insertPredictionAuditSchema>;
export type PredictionAudit = typeof predictionAudit.$inferSelect;
export type InsertRaceSchedule = z.infer<typeof insertRaceScheduleSchema>;
export type RaceSchedule = typeof raceSchedule.$inferSelect;

export const raceResultsHistory = pgTable("race_results_history", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  horseId: text("horse_id").notNull(),
  horseName: text("horse_name").notNull(),
  raceId: text("race_id").notNull(),
  track: text("track").notNull(),
  raceDate: text("race_date").notNull(),
  distanceM: integer("distance_m"),
  raceClass: text("race_class"),
  classLevel: integer("class_level"),
  going: text("going"),
  position: integer("position"),
  marginLengths: real("margin_lengths"),
  weightKg: real("weight_kg"),
  jockey: text("jockey"),
  jockeyId: text("jockey_id"),
  barrier: integer("barrier"),
  spOdds: real("sp_odds"),
  fieldSize: integer("field_size"),
  raceName: text("race_name"),
  raceNumber: integer("race_number"),
  formString: text("form_string"),
  opponentsJson: jsonb("opponents_json"),
  createdAt: timestamp("created_at").defaultNow(),
});

export const frankingScores = pgTable("franking_scores", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  horseId: text("horse_id").notNull(),
  horseName: text("horse_name").notNull(),
  globalElo: real("global_elo"),
  frankingScore: real("franking_score"),
  frankingConfidence: real("franking_confidence"),
  antiFranked: boolean("anti_franked").default(false),
  fieldStrengthAvg: real("field_strength_avg"),
  formQualityTrend: real("form_quality_trend"),
  bestAdjustedMargin: real("best_adjusted_margin"),
  collateralAdvantage: real("collateral_advantage"),
  dataPoints: integer("data_points"),
  lastRaceDate: text("last_race_date"),
  detailsJson: jsonb("details_json"),
  computedAt: timestamp("computed_at").defaultNow(),
});

export const sectionalTimes = pgTable("sectional_times", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  raceResultsHistoryId: varchar("race_results_history_id"),
  track: text("track").notNull(),
  raceDate: text("race_date").notNull(),
  raceNumber: integer("race_number").notNull(),
  raceName: text("race_name"),
  horseName: text("horse_name").notNull(),
  saddleNumber: integer("saddle_number"),
  distanceM: integer("distance_m"),
  winningTime: text("winning_time"),
  trackConfig: text("track_config"),
  splitsJson: jsonb("splits_json"),
  last200mSpeed: real("last_200m_speed"),
  last200mTime: real("last_200m_time"),
  last400mSpeed: real("last_400m_speed"),
  last400mTime: real("last_400m_time"),
  last600mSpeed: real("last_600m_speed"),
  last600mTime: real("last_600m_time"),
  last800mSpeed: real("last_800m_speed"),
  last800mTime: real("last_800m_time"),
  finishingBurst: real("finishing_burst"),
  avgSpeed: real("avg_speed"),
  // Phase 2: Biomechanical & statistical sectional features
  lambdaDecay: real("lambda_decay"),              // Velocity decay constant λ (Ward-Smith 1985)
  svi: real("svi"),                               // Sustained Velocity Index: mean(last 600m) / mean(all)
  rsi: real("rsi"),                               // Race Shape Index: first_half_time / second_half_time
  z200m: real("z_200m"),                          // Z-score at mark 200 vs race field
  z400m: real("z_400m"),                          // Z-score at mark 400 vs race field
  z600m: real("z_600m"),                          // Z-score at mark 600 vs race field
  z800m: real("z_800m"),                          // Z-score at mark 800 vs race field
  tripCostSeconds: real("trip_cost_seconds"),      // Time-equivalent lost to wide barrier (Thorograph)
  variantAdjusted200m: real("variant_adjusted_200m"), // Track-variant-corrected last_200m_speed
  variantAdjusted400m: real("variant_adjusted_400m"),
  variantAdjusted600m: real("variant_adjusted_600m"),
  variantAdjusted800m: real("variant_adjusted_800m"),
  source: text("source").default("racing_qld"),
  createdAt: timestamp("created_at").defaultNow(),
});

export const blackbookEntries = pgTable("blackbook_entries", {
  id: text("id").primaryKey(),
  canonicalHorseKey: text("canonical_horse_key").notNull(),
  horseId: text("horse_id"),
  horseName: text("horse_name").notNull(),
  sourceRaceId: text("source_race_id"),
  sourceTrack: text("source_track").notNull(),
  sourceRaceDate: text("source_race_date").notNull(),
  sourceRaceNumber: integer("source_race_number").notNull(),
  sourceRaceName: text("source_race_name"),
  sourceDistanceM: integer("source_distance_m"),
  sourceGoing: text("source_going"),
  sourceRaceClass: text("source_race_class"),
  sourceFieldSize: integer("source_field_size"),
  sourcePosition: integer("source_position"),
  sourceBarrier: integer("source_barrier"),
  sourceMarginLengths: real("source_margin_lengths"),
  primaryReason: text("primary_reason").notNull(),
  secondaryEvidenceTags: jsonb("secondary_evidence_tags"),
  sourceComment: text("source_comment"),
  sourceIncidentsJson: jsonb("source_incidents_json"),
  incidentSummary: text("incident_summary"),
  last600mTime: real("last_600m_time"),
  last400mTime: real("last_400m_time"),
  last200mTime: real("last_200m_time"),
  fieldAvgLast600m: real("field_avg_last_600m"),
  fieldAvgLast400m: real("field_avg_last_400m"),
  fieldAvgLast200m: real("field_avg_last_200m"),
  sectionalDelta600m: real("sectional_delta_600m"),
  sectionalDelta400m: real("sectional_delta_400m"),
  sectionalDelta200m: real("sectional_delta_200m"),
  winnerLast600m: real("winner_last_600m"),
  winnerLast400m: real("winner_last_400m"),
  winnerLast200m: real("winner_last_200m"),
  frankingScore: real("franking_score"),
  frankingConfidence: real("franking_confidence"),
  idealConditionsJson: jsonb("ideal_conditions_json"),
  evidenceJson: jsonb("evidence_json"),
  entryAnalysisJson: jsonb("entry_analysis_json"),
  intakeConfidence: real("intake_confidence"),
  readinessBand: text("readiness_band"),
  status: text("status").default("waiting"),
  expiryReason: text("expiry_reason"),
  createdAt: timestamp("created_at").defaultNow(),
  updatedAt: timestamp("updated_at").defaultNow(),
});

export const blackbookEntryRuns = pgTable("blackbook_entry_runs", {
  id: text("id").primaryKey(),
  blackbookEntryId: text("blackbook_entry_id").notNull().references(() => blackbookEntries.id, { onDelete: "cascade" }),
  canonicalHorseKey: text("canonical_horse_key").notNull(),
  horseName: text("horse_name").notNull(),
  track: text("track").notNull(),
  raceDate: text("race_date").notNull(),
  raceNumber: integer("race_number").notNull(),
  raceName: text("race_name"),
  offTime: text("off_time"),
  distanceM: integer("distance_m"),
  going: text("going"),
  raceClass: text("race_class"),
  fieldSize: integer("field_size"),
  barrier: integer("barrier"),
  daysSinceSourceRun: integer("days_since_source_run"),
  tempoSummary: text("tempo_summary"),
  tempoLeaderCount: integer("tempo_leader_count"),
  marketPrice: real("market_price"),
  truePrice: real("true_price"),
  marketImpliedProb: real("market_implied_prob"),
  modelWinProb: real("model_win_prob"),
  valueEdgePct: real("value_edge_pct"),
  readinessScore: integer("readiness_score"),
  readinessBand: text("readiness_band"),
  verdict: text("verdict"),
  status: text("status").default("nominated"),
  breakdownJson: jsonb("breakdown_json"),
  conditionAlignmentJson: jsonb("condition_alignment_json"),
  raceDayBriefJson: jsonb("race_day_brief_json"),
  createdAt: timestamp("created_at").defaultNow(),
  updatedAt: timestamp("updated_at").defaultNow(),
});

export const blackbookAlerts = pgTable("blackbook_alerts", {
  id: text("id").primaryKey(),
  eventKey: text("event_key").notNull(),
  blackbookEntryId: text("blackbook_entry_id").notNull().references(() => blackbookEntries.id, { onDelete: "cascade" }),
  blackbookEntryRunId: text("blackbook_entry_run_id").references(() => blackbookEntryRuns.id, { onDelete: "cascade" }),
  horseName: text("horse_name").notNull(),
  track: text("track"),
  raceDate: text("race_date"),
  raceNumber: integer("race_number"),
  alertType: text("alert_type").notNull(),
  severity: text("severity").default("info"),
  title: text("title").notNull(),
  message: text("message").notNull(),
  readinessScore: integer("readiness_score"),
  createdAt: timestamp("created_at").defaultNow(),
  updatedAt: timestamp("updated_at").defaultNow(),
});

export const trackDayBias = pgTable("track_day_bias", {
  id: text("id").primaryKey(),
  track: text("track").notNull(),
  raceDate: text("race_date").notNull(),
  biasLabel: text("bias_label").notNull(),
  confidence: text("confidence").notNull(),
  racesSampled: integer("races_sampled").default(0),
  insideWinRate: real("inside_win_rate"),
  middleWinRate: real("middle_win_rate"),
  outsideWinRate: real("outside_win_rate"),
  avgWinnerBarrierRatio: real("avg_winner_barrier_ratio"),
  statsJson: jsonb("stats_json"),
  createdAt: timestamp("created_at").defaultNow(),
  updatedAt: timestamp("updated_at").defaultNow(),
});

export const insertRaceResultsHistorySchema = createInsertSchema(raceResultsHistory).omit({
  id: true,
  createdAt: true,
});

export const insertFrankingScoresSchema = createInsertSchema(frankingScores).omit({
  id: true,
  computedAt: true,
});

export const insertBlackbookEntriesSchema = createInsertSchema(blackbookEntries).omit({
  createdAt: true,
  updatedAt: true,
});

export const insertBlackbookEntryRunsSchema = createInsertSchema(blackbookEntryRuns).omit({
  createdAt: true,
  updatedAt: true,
});

export const insertBlackbookAlertsSchema = createInsertSchema(blackbookAlerts).omit({
  createdAt: true,
  updatedAt: true,
});

export const insertTrackDayBiasSchema = createInsertSchema(trackDayBias).omit({
  createdAt: true,
  updatedAt: true,
});

export type InsertRaceResultsHistory = z.infer<typeof insertRaceResultsHistorySchema>;
export type RaceResultsHistory = typeof raceResultsHistory.$inferSelect;
export type InsertFrankingScores = z.infer<typeof insertFrankingScoresSchema>;
export type FrankingScores = typeof frankingScores.$inferSelect;
export type InsertBlackbookEntry = z.infer<typeof insertBlackbookEntriesSchema>;
export type BlackbookEntry = typeof blackbookEntries.$inferSelect;
export type InsertBlackbookEntryRun = z.infer<typeof insertBlackbookEntryRunsSchema>;
export type BlackbookEntryRun = typeof blackbookEntryRuns.$inferSelect;
export type InsertBlackbookAlert = z.infer<typeof insertBlackbookAlertsSchema>;
export type BlackbookAlert = typeof blackbookAlerts.$inferSelect;
export type InsertTrackDayBias = z.infer<typeof insertTrackDayBiasSchema>;
export type TrackDayBias = typeof trackDayBias.$inferSelect;

export const chatRequestSchema = z.object({
  message: z.string().min(1),
});

export const chatModeStateSchema = z.object({
  brain: z.boolean().default(false),
  search: z.boolean().default(false),
});

export const chatRaceContextSchema = z.object({
  date: z.string().min(1),
  track: z.string().min(1),
  raceNumber: z.number().int().positive(),
  raceName: z.string().optional(),
  distance: z.string().optional(),
  going: z.string().optional(),
});

export const chatCompletionRequestSchema = chatRequestSchema.extend({
  sessionId: z.string().optional(),
  turnId: z.string().optional(),
  modes: chatModeStateSchema.optional(),
  raceContext: chatRaceContextSchema.optional(),
});

export type ChatModeState = z.infer<typeof chatModeStateSchema>;
export type ChatRaceContext = z.infer<typeof chatRaceContextSchema>;
export type ChatCompletionRequest = z.infer<typeof chatCompletionRequestSchema>;

export type ChatAnswerSource = "local" | "web" | "hybrid" | "error";
export type ChatTraceSectionKind = "thinking" | "steps" | "sources" | "why";

export interface ChatCitationLink {
  id: string;
  label: string;
  url: string;
  domain: string;
  sentence?: string;
}

export interface ChatTraceSection {
  id: string;
  kind: ChatTraceSectionKind;
  title: string;
  summary: string;
  bullets: string[];
}

export interface ChatSearchQueryTrace {
  query: string;
  rationale?: string;
}

export interface ChatSearchResultTrace {
  id: string;
  title: string;
  url: string;
  domain: string;
  query: string;
  snippet: string;
  selected: boolean;
  reason?: string;
  extracted?: string;
}

export interface ChatTrace {
  label: string;
  headline: string;
  summary: string;
  sections: ChatTraceSection[];
  searchQueries: ChatSearchQueryTrace[];
  searchResults: ChatSearchResultTrace[];
  finalWhy: string;
}

export interface ChatCompletionResponse {
  response: string;
  mode: ChatModeState;
  answerSource: ChatAnswerSource;
  trace: ChatTrace;
  citations: ChatCitationLink[];
  warnings: string[];
  turnId?: string;
  promptVersion?: string;
}

export const simulationRequestSchema = z.object({
  track: z.string().min(1),
  race: z.string().min(1),
  iterations: z.number().min(1000).max(100000),
});

export interface SimulationResult {
  horses: Array<{
    name: string;
    winPercentage: number;
    placePercentage: number;
    impliedOdds: number;
    valueRating: "high" | "medium" | "low";
  }>;
  convergenceData: Array<{ iteration: number; variance: number }>;
}

export interface Bet {
  id: string;
  track: string;
  raceNumber: number;
  raceName?: string;
  distance?: string;
  offTime?: string;
  horseName: string;
  horseNumber?: string;
  barrier?: string;
  jockey?: string;
  trainer?: string;
  form?: string;
  modelProbability: number;
  marketOdds: number;
  impliedOdds?: number;
  expectedValue: number;
  kellyStake: number;
  confidence: "high" | "medium" | "low";
  // Monte Carlo simulation details
  winPercentage: number;
  placePercentage: number;
  ciLower: number;
  ciUpper: number;
  expectedPosition: number;
  positionStdDev: number;
  stabilityScore: number;
  runningStyle: string;
  paceSplits: Record<string, number>;
  edge: number;
  valueRating: "high" | "medium" | "low";
  hasMarketOdds: boolean;
}

export interface BettingData {
  bets: Bet[];
  summary: {
    totalBets: number;
    averageEV: number;
    recommendedAllocation: number;
    highConfidenceBets?: number;
    meetsAnalyzed?: number;
    racesAnalyzed?: number;
  };
  dataSource?: string;
  metadata?: {
    model: string;
    iterationsPerRace: number;
    timestamp: string;
    date: string;
  };
  message?: string;
}

export interface FormGuideData {
  track: string;
  raceNumber: number;
  raceName: string;
  distance: number;
  horses: Array<{
    id: string;
    name: string;
    trainer: string;
    jockey: string;
    weight: number;
    barrier: number;
    form: string;
    rating: number;
    trend: "up" | "down" | "stable";
  }>;
}

// Bet Slip Types
export type StakingMethod = "kelly" | "fractional_kelly" | "level_stakes" | "proportional" | "percentage";
export type BetSlipBetType = "win" | "place" | "each_way";

export interface BetSlipSelection {
  id: string;
  horseName: string;
  track: string;
  raceNumber: number;
  raceDate: string;
  raceTime: string;
  odds: number;
  predictedWinProb: number;
  confidenceScore: number;
  betType: BetSlipBetType;
  placeOdds?: number;
  reasons: string[];
  addedAt: string;
}

export interface StakeRecommendation {
  selectionId: string;
  horseName: string;
  recommendedStake: number;
  stakePercentage: number;
  potentialReturn: number;
  potentialProfit: number;
  expectedValue: number;
  riskLevel: "low" | "medium" | "high";
  reasoning: string;
}

export interface MultiBetOption {
  type: "double" | "treble" | "accumulator";
  name: string;
  selections: string[];
  combinedOdds: number;
  combinedProb: number;
  recommendedStakePct: number;
}

export interface BetSlipSummary {
  totalSelections: number;
  totalStake: number;
  totalPotentialReturn: number;
  totalPotentialProfit: number;
  combinedProbability: number;
  expectedValue: number;
  riskAssessment: "none" | "unknown" | "low" | "medium" | "high";
  bankrollPercentageUsed: number;
  recommendations: StakeRecommendation[];
  multiBetOptions: MultiBetOption[];
}

export interface BetSlipState {
  selections: BetSlipSelection[];
  selectionCount: number;
  bankroll: number;
  stakingMethod: StakingMethod;
  kellyFraction: number;
  maxStakePercentage: number;
}

export const addToSlipSchema = z.object({
  horseName: z.string(),
  track: z.string(),
  raceNumber: z.number(),
  raceDate: z.string().optional(),
  raceTime: z.string().optional(),
  odds: z.number(),
  predictedWinProb: z.number(),
  confidenceScore: z.number().optional(),
  betType: z.enum(["win", "place", "each_way"]).optional(),
  reasons: z.array(z.string()).optional(),
});

export const setBankrollSchema = z.object({
  amount: z.number().positive(),
});

export const setStakingMethodSchema = z.object({
  method: z.enum(["kelly", "fractional_kelly", "level_stakes", "proportional", "percentage"]),
  kellyFraction: z.number().optional(),
  maxStakePercentage: z.number().optional(),
});

export const quickCalculateSchema = z.object({
  bankroll: z.number().positive(),
  stakingMethod: z.enum(["kelly", "fractional_kelly", "level_stakes", "proportional", "percentage"]).optional(),
});
