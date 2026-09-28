export interface Player {
  name: string
  age: number
  height: number
  weight: number
  position: string
  nationality: string
  club: string
  overall: number
  preferred_foot: string
  injury: string
  last_updated: string
  long_term_goals?: string[] | string | null
  attributes: Record<string, Record<string, number>>
}
export interface TrainingRecord {
  week: string
  date_range: string
  focus: string
  weekly_load: number
  avg_rpe: number
  notes: string
  training_sessions: { day: string; type: string; duration_min: number; intensity: string }[]
}
export interface MatchRecord {
  date: string
  opponent: string
  competition: string
  result: string
  minutes_played: number
  goals: number
  assists: number
  rating: number
  position: string
  notes: string
}
