import { request } from './client'
import type { Player, TrainingRecord, MatchRecord } from '../types/player'
export const getPlayer = () => request<Player>('/player')
export const getTrainingHistory = () => request<TrainingRecord[]>('/player/training-history')
export const getMatchHistory = () => request<MatchRecord[]>('/player/match-history')
