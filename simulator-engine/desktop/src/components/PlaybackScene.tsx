import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Line, OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import type { PlaybackFrame, PlaybackMetadata, PlaybackParticle } from "../types";

export interface LayerState {
  ground: boolean;
  stack: boolean;
  flightPath: boolean;
  fine: boolean;
  coarse: boolean;
  deposited: boolean;
  wind: boolean;
  sensor: boolean;
  voxelGrid: boolean;
}

export function PlaybackScene({
  frame,
  metadata,
  layers,
  density,
  renderLimit,
  followDrone,
  resetToken,
  onRenderCount,
}: {
  frame: PlaybackFrame;
  metadata: PlaybackMetadata;
  layers: LayerState;
  density: number;
  renderLimit: number;
  followDrone: boolean;
  resetToken: number;
  onRenderCount: (count: number) => void;
}) {
  const [sceneState, setSceneState] = useState<"loading" | "ready" | "fallback">("loading");
  const flightPath = metadata.flight_path?.length
    ? metadata.flight_path
    : [];
  // Keep overview bounds stable while the timeline advances so an operator's
  // orbit-camera position is never reset by a new frame.
  const bounds = useMemo(() => calculateBounds(flightPath, frame, metadata), [flightPath, metadata]);
  const visibleParticles = useMemo(() => {
    const filtered = frame.particles.filter((particle) => {
      const deposited = particle.state === "deposited" || particle.particle_class === "deposited";
      if (deposited) return layers.deposited;
      return particle.particle_class === "fine" ? layers.fine : layers.coarse;
    });
    const allowed = Math.max(1, Math.min(renderLimit, Math.round(renderLimit * density / 100)));
    if (filtered.length <= allowed) return filtered;
    return Array.from({ length: allowed }, (_, index) => filtered[Math.floor(index * filtered.length / allowed)]);
  }, [frame.particles, layers.fine, layers.coarse, layers.deposited, density, renderLimit]);

  useLayoutEffect(() => onRenderCount(visibleParticles.length), [visibleParticles.length, onRenderCount]);

  useEffect(() => {
    if (sceneState !== "loading") return;
    const timeout = window.setTimeout(() => setSceneState("fallback"), 7000);
    return () => window.clearTimeout(timeout);
  }, [sceneState]);

  const fine = visibleParticles.filter((particle) => particle.particle_class === "fine" && particle.state !== "deposited");
  const coarse = visibleParticles.filter((particle) => particle.particle_class === "coarse" && particle.state !== "deposited");
  const deposited = visibleParticles.filter((particle) => particle.state === "deposited" || particle.particle_class === "deposited");
  const dronePosition = localToWorld(frame.drone.x, frame.drone.y, frame.drone.z);
  const source = metadata.source ?? { x: 0, y: 0, z: 0, stack_height_m: 60 };
  const sourcePosition = localToWorld(source.x, source.y, source.z);
  const pathPositions = flightPath.map((point) => localToWorld(point.x, point.y, point.z));

  if (sceneState === "fallback") {
    return <TopDownFallback frame={frame} metadata={metadata} particles={visibleParticles} />;
  }

  return (
    <div className="scene-canvas" aria-label="Synchronized three-dimensional drone and particle plume playback">
      <Canvas
        dpr={window.matchMedia("(max-width: 820px)").matches ? 1 : [1, 1.75]}
        camera={{ fov: 48, near: 0.1, far: 100000, position: [bounds.radius * 0.75, bounds.radius * 0.6, bounds.radius * 0.85] }}
        gl={{ antialias: true, powerPreference: "high-performance" }}
        onCreated={() => setSceneState("ready")}
      >
        <color attach="background" args={["#071117"]} />
        <fog attach="fog" args={["#071117", bounds.radius * 1.1, bounds.radius * 4]} />
        <ambientLight intensity={0.58} />
        <hemisphereLight color="#b7e5ff" groundColor="#10231e" intensity={1.15} />
        <directionalLight position={[bounds.radius, bounds.radius * 1.3, bounds.radius * 0.8]} intensity={1.8} color="#fff4db" castShadow={false} />
        {layers.ground && <Ground size={bounds.radius * 2.4} grid={layers.voxelGrid} />}
        {layers.flightPath && pathPositions.length > 1 && <Line points={pathPositions} color="#60d9ff" lineWidth={1.6} transparent opacity={0.75} />}
        {layers.stack && <Smokestack position={sourcePosition} height={source.stack_height_m} diameter={metadata.source && "diameter_m" in metadata.source ? Number((metadata.source as unknown as { diameter_m: number }).diameter_m) : 3} />}
        <ParticleInstances particles={fine} color="#36d9ff" radius={1.35} opacity={0.68} />
        <ParticleInstances particles={coarse} color="#ff9a54" radius={1.85} opacity={0.78} />
        <ParticleInstances particles={deposited} color="#bb7b4c" radius={1.2} opacity={0.9} flatten />
        <Drone position={dronePosition} />
        {layers.sensor && <SensorRegion position={dronePosition} dimensions={frame.sensor_region ?? { size_x_m: 20, size_y_m: 20, size_z_m: 10 }} />}
        {layers.wind && <WindArrow position={dronePosition} directionFromDeg={frame.wind_direction_deg} speed={frame.wind_speed_mps} />}
        <CameraRig drone={dronePosition} follow={followDrone} resetToken={resetToken} bounds={bounds} />
        <OrbitControls enabled={!followDrone} makeDefault target={[bounds.center.x, 20, bounds.center.z]} minDistance={5} maxDistance={bounds.radius * 5} maxPolarAngle={Math.PI * 0.495} />
      </Canvas>
      {sceneState === "loading" && <div className="scene-loading" role="status">Preparing interactive 3D scene…</div>}
      <div className="scene-axis"><span className="north">N</span><span className="east">E</span><i /></div>
      <div className="scene-scale"><span>{formatSceneDistance(bounds.radius / 4)}</span><i /></div>
    </div>
  );
}

function TopDownFallback({ frame, metadata, particles }: { frame: PlaybackFrame; metadata: PlaybackMetadata; particles: PlaybackParticle[] }) {
  const source = metadata.source ?? { x: 0, y: 0, z: 0, stack_height_m: 60 };
  const flightPath = metadata.flight_path ?? [];
  const points = [...flightPath.map((point) => [point.x, point.y]), [source.x, source.y], [frame.drone.x, frame.drone.y], ...particles.map((point) => [point.x, point.y])];
  const xs = points.map(([x]) => x);
  const ys = points.map(([, y]) => y);
  const minX = Math.min(...xs) - 20;
  const maxX = Math.max(...xs) + 20;
  const minY = Math.min(...ys) - 20;
  const maxY = Math.max(...ys) + 20;
  const project = (x: number, y: number) => [30 + 740 * (x - minX) / Math.max(maxX - minX, 1), 30 + 420 * (maxY - y) / Math.max(maxY - minY, 1)];
  const [sourceX, sourceY] = project(source.x, source.y);
  const [droneX, droneY] = project(frame.drone.x, frame.drone.y);
  const displayParticles = particles.length <= 500 ? particles : Array.from({ length: 500 }, (_, index) => particles[Math.floor(index * particles.length / 500)]);
  return <div className="scene-fallback" role="status">
    <div className="scene-fallback-heading"><strong>Top-down trace preview</strong><span>3D rendering is unavailable in this browser; timeline, measurements and audit remain active.</span></div>
    <svg viewBox="0 0 800 480" role="img" aria-label="Top-down plume parcels, source, flight path and drone">
      {[1, 2, 3, 4].map((index) => <g key={index}><line x1={index * 160} y1="0" x2={index * 160} y2="480" /><line x1="0" y1={index * 96} x2="800" y2={index * 96} /></g>)}
      {flightPath.length > 1 && <polyline points={flightPath.map((point) => project(point.x, point.y).join(",")).join(" ")} className="scene-fallback-path" />}
      {displayParticles.map((particle) => { const [x, y] = project(particle.x, particle.y); return <circle key={particle.id} cx={x} cy={y} r="2.1" className={particle.particle_class === "fine" ? "fine" : "coarse"} />; })}
      <circle cx={sourceX} cy={sourceY} r="8" className="scene-fallback-source" />
      <circle cx={droneX} cy={droneY} r="7" className="scene-fallback-drone" />
    </svg>
    <div className="scene-fallback-legend"><span>● PM2.5 parcel</span><span>● Coarse parcel</span><span>◉ Source</span><span>◆ Drone</span></div>
  </div>;
}

function Ground({ size, grid }: { size: number; grid: boolean }) {
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.35, 0]} receiveShadow>
        <planeGeometry args={[size, size]} />
        <meshStandardMaterial color="#10251f" roughness={1} metalness={0} />
      </mesh>
      <gridHelper args={[size, grid ? 80 : 24, grid ? "#4a6265" : "#294542", "#18332f"]} position={[0, -0.2, 0]} />
    </group>
  );
}

function Smokestack({ position, height, diameter }: { position: [number, number, number]; height: number; diameter: number }) {
  const visualDiameter = Math.max(2, diameter);
  return (
    <group position={position}>
      <mesh position={[0, height / 2, 0]}>
        <cylinderGeometry args={[visualDiameter / 2, visualDiameter * 0.72, height, 18]} />
        <meshStandardMaterial color="#a9b4b8" roughness={0.75} metalness={0.25} />
      </mesh>
      <mesh position={[0, height + 0.8, 0]}>
        <cylinderGeometry args={[visualDiameter * 0.62, visualDiameter * 0.62, 1.6, 18]} />
        <meshStandardMaterial color="#f07843" roughness={0.55} />
      </mesh>
      <mesh position={[0, 0.5, 0]}>
        <cylinderGeometry args={[visualDiameter * 2.1, visualDiameter * 2.1, 1, 20]} />
        <meshStandardMaterial color="#59676b" />
      </mesh>
    </group>
  );
}

function ParticleInstances({ particles, color, radius, opacity, flatten = false }: { particles: PlaybackParticle[]; color: string; radius: number; opacity: number; flatten?: boolean }) {
  const ref = useRef<THREE.InstancedMesh>(null);
  useLayoutEffect(() => {
    const mesh = ref.current;
    if (!mesh) return;
    const matrix = new THREE.Matrix4();
    const position = new THREE.Vector3();
    const quaternion = new THREE.Quaternion();
    const scale = new THREE.Vector3();
    particles.forEach((particle, index) => {
      const world = localToWorld(particle.x, particle.y, particle.z);
      position.set(...world);
      const massScale = particle.mass_g ? Math.min(1.8, Math.max(0.7, Math.cbrt(particle.mass_g * 1000))) : 1;
      scale.set(massScale, flatten ? massScale * 0.25 : massScale, massScale);
      matrix.compose(position, quaternion, scale);
      mesh.setMatrixAt(index, matrix);
    });
    mesh.count = particles.length;
    mesh.instanceMatrix.needsUpdate = true;
    mesh.computeBoundingSphere();
  }, [particles, flatten]);
  if (!particles.length) return null;
  return (
    <instancedMesh ref={ref} args={[undefined, undefined, particles.length]} frustumCulled>
      <sphereGeometry args={[radius, 7, 6]} />
      <meshStandardMaterial color={color} transparent opacity={opacity} depthWrite={false} roughness={0.7} />
    </instancedMesh>
  );
}

function Drone({ position }: { position: [number, number, number] }) {
  return (
    <group position={position} scale={1.45}>
      <mesh><boxGeometry args={[4.8, 1.25, 2.7]} /><meshStandardMaterial color="#e5eef0" metalness={0.25} roughness={0.45} /></mesh>
      <mesh position={[0, -0.8, 0]}><boxGeometry args={[1.4, 0.7, 1.1]} /><meshStandardMaterial color="#152d37" /></mesh>
      {[[3.4, 2.7], [3.4, -2.7], [-3.4, 2.7], [-3.4, -2.7]].map(([x, z], index) => <group key={index} position={[x, 0.1, z]}><mesh rotation={[0, 0, x > 0 ? -0.22 : 0.22]} position={[-x * 0.5, 0, -z * 0.5]}><boxGeometry args={[4.2, 0.22, 0.25]} /><meshStandardMaterial color="#829299" /></mesh><mesh position={[0, 0.28, 0]}><cylinderGeometry args={[0.28, 0.32, 0.45, 10]} /><meshStandardMaterial color="#343f43" /></mesh><mesh position={[0, 0.55, 0]} rotation={[0, index % 2 ? 0.35 : -0.35, 0]}><boxGeometry args={[4.3, 0.06, 0.25]} /><meshStandardMaterial color="#69deee" emissive="#1d9fb3" emissiveIntensity={0.6} /></mesh></group>)}
      <mesh position={[2.5, 0.2, 0]} rotation={[0, 0, -Math.PI / 2]}><coneGeometry args={[0.8, 1.5, 12]} /><meshStandardMaterial color="#f3924e" /></mesh>
    </group>
  );
}

function SensorRegion({ position, dimensions }: { position: [number, number, number]; dimensions: { size_x_m: number; size_y_m: number; size_z_m: number } }) {
  return (
    <mesh position={position}>
      <boxGeometry args={[dimensions.size_x_m, dimensions.size_z_m, dimensions.size_y_m]} />
      <meshBasicMaterial color="#f3dc68" wireframe transparent opacity={0.7} />
    </mesh>
  );
}

function WindArrow({ position, directionFromDeg, speed }: { position: [number, number, number]; directionFromDeg: number; speed: number }) {
  const helper = useMemo(() => {
    const radians = directionFromDeg * Math.PI / 180;
    const direction = new THREE.Vector3(-Math.sin(radians), 0, Math.cos(radians)).normalize();
    return new THREE.ArrowHelper(direction, new THREE.Vector3(0, 0, 0), Math.max(14, Math.min(50, 9 + speed * 5)), 0x5de3ad, 5, 3);
  }, [directionFromDeg, speed]);
  return <primitive object={helper} position={[position[0], position[1] + 12, position[2]]} />;
}

function CameraRig({ drone, follow, resetToken, bounds }: { drone: [number, number, number]; follow: boolean; resetToken: number; bounds: SceneBounds }) {
  const { camera } = useThree();
  const desired = useMemo(() => new THREE.Vector3(), []);
  const target = useMemo(() => new THREE.Vector3(), []);
  useLayoutEffect(() => {
    if (follow) return;
    camera.position.set(bounds.center.x + bounds.radius * 0.7, bounds.radius * 0.65, bounds.center.z + bounds.radius * 0.9);
    camera.lookAt(bounds.center.x, Math.min(35, bounds.radius * 0.15), bounds.center.z);
    camera.updateProjectionMatrix();
  }, [camera, resetToken, bounds, follow]);
  useFrame(() => {
    if (!follow) return;
    target.set(drone[0], drone[1], drone[2]);
    desired.set(drone[0] + 28, drone[1] + 18, drone[2] + 32);
    camera.position.lerp(desired, 0.075);
    camera.lookAt(target);
  });
  return null;
}

interface SceneBounds { center: { x: number; z: number }; radius: number }

function calculateBounds(path: Array<{ x: number; y: number; z: number }>, frame: PlaybackFrame, metadata: PlaybackMetadata): SceneBounds {
  const xs = path.map((point) => point.x).concat(frame.drone.x, metadata.source?.x ?? 0);
  const worldZ = path.map((point) => -point.y).concat(-frame.drone.y, -(metadata.source?.y ?? 0));
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minZ = Math.min(...worldZ);
  const maxZ = Math.max(...worldZ);
  const center = { x: (minX + maxX) / 2, z: (minZ + maxZ) / 2 };
  return { center, radius: Math.max(120, (maxX - minX) * 0.72, (maxZ - minZ) * 0.72) };
}

function localToWorld(xEast: number, yNorth: number, zUp: number): [number, number, number] {
  return [xEast, Math.max(0, zUp), -yNorth];
}

function formatSceneDistance(value: number) {
  if (value >= 1000) return `${(value / 1000).toFixed(1)} km`;
  return `${Math.round(value)} m`;
}
