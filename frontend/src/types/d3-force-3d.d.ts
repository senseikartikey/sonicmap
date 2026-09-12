declare module "d3-force-3d" {
  export interface SimulationNodeDatum {
    index?: number;
    x?: number;
    y?: number;
    z?: number;
    vx?: number;
    vy?: number;
    vz?: number;
  }

  interface ConfigurableForce<N extends SimulationNodeDatum> {
    strength(value: number | ((node: N) => number)): this;
    iterations(value: number): this;
    distanceMax(value: number): this;
  }

  export interface Simulation<N extends SimulationNodeDatum> {
    force(name: string, force: ConfigurableForce<N>): this;
    stop(): this;
    tick(iterations?: number): this;
  }

  export function forceSimulation<N extends SimulationNodeDatum>(nodes?: N[], dimensions?: number): Simulation<N>;
  export function forceCollide<N extends SimulationNodeDatum>(radius?: number | ((node: N) => number)): ConfigurableForce<N>;
  export function forceManyBody<N extends SimulationNodeDatum>(): ConfigurableForce<N>;
  export function forceX<N extends SimulationNodeDatum>(x?: number | ((node: N) => number)): ConfigurableForce<N>;
  export function forceY<N extends SimulationNodeDatum>(y?: number | ((node: N) => number)): ConfigurableForce<N>;
  export function forceZ<N extends SimulationNodeDatum>(z?: number | ((node: N) => number)): ConfigurableForce<N>;
}
