import * as live from './client'
import * as replay from './replay'

export const REPLAY = import.meta.env.VITE_REPLAY === '1'

// One switch, so no component knows which mode it runs in.
const impl = REPLAY ? replay : live

export const { listShows, getShow, audioUrl, getOptions, createShow } = impl
export type * from './types'
