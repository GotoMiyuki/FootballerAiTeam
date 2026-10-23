export interface Player {
  name: string | null
  age: number | null
  height: number | null
  weight: number | null
  position: string | null
  nationality: string | null
  club: string | null
  overall: number | null
  preferred_foot: string | null
  injury: string | null
  last_updated: string | null
  long_term_goals?: string[] | string | null
  metadata?: { source_type?: string; sources?: Record<string, unknown>; state_version: string; snapshot_id: string; context: { career_id: string; branch_id: string; player_id: string }; quality_flags: string[] }
  attributes: Record<string, Record<string, number | null>> | null
}
export interface TrainingRecord {
  week: string | null
  date_range: string | null
  focus: string | null
  weekly_load: number | null
  avg_rpe: number | null
  notes: string | null
  training_sessions: { day: string; type: string; duration_min: number | null; intensity: string }[]
}
export interface MatchRecord {
  date: string | null
  opponent: string | null
  competition: string | null
  result: string | null
  minutes_played: number | null
  goals: number | null
  assists: number | null
  rating: number | null
  position: string | null
  notes: string | null
}
