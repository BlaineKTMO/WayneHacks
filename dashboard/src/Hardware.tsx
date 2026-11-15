// /demo: photos of the two forklifts and what they're made of.
import './hardware.css'

const PARTS: { name: string; what: string }[] = [
  { name: 'Free-WiLi', what: 'The brain on each forklift. Our firmware reads drive commands from the edge laptop, runs the motors through PWM, shows the forklift’s ID and state on its screen, and stops everything if commands go quiet for half a second.' },
  { name: 'AprilTags', what: 'Printed black-and-white markers on each roof. The ceiling camera reads them to get every forklift’s position and heading, so the trucks carry no sensors of their own.' },
  { name: 'L298N motor drivers', what: 'Cheap H-bridge boards that take the Free-WiLi’s signals and supply power to the drive motors and the fork lift.' },
  { name: 'Arduino robot car kits', what: 'FL-02 is built on a hobby car kit: the chassis plate, yellow gear motors and rubber wheels all came in the box.' },
  { name: '3D-printed parts', what: 'FL-01’s chassis, mast and forks are printed. The fork carriage slides up the red mast so it can actually lift a pallet.' },
  { name: 'Mecanum wheels', what: 'FL-01 rolls on mecanum wheels, which let it turn on the spot in tight aisles.' },
]

export function Hardware() {
  return (
    <>
      <figure className="hw-hero">
        <img src="/hardware/fleet-top.jpg" alt="FL-01 and FL-02 from above, each with an AprilTag on its roof" loading="lazy" />
        <figcaption>FL-01 (left) and FL-02 (right) from where the ceiling camera sees them. The square codes on top are the AprilTags.</figcaption>
      </figure>

      <div className="hw-units">
        <figure className="hw-unit">
          <img src="/hardware/fl-01.jpg" alt="FL-01: orange 3D-printed chassis with a red mast, forks and a Free-WiLi showing FL-01 IDLE" loading="lazy" />
          <figcaption><b>FL-01</b><span>3D-printed chassis and mast with working forks, mecanum wheels, a Free-WiLi and two L298N drivers.</span></figcaption>
        </figure>
        <figure className="hw-unit">
          <img src="/hardware/fl-02.jpg" alt="FL-02: Arduino car kit chassis with yellow wheels and a Free-WiLi on top" loading="lazy" />
          <figcaption><b>FL-02</b><span>Built from an Arduino car kit, with a Free-WiLi on top and L298N drivers for the four gear motors.</span></figcaption>
        </figure>
        <figure className="hw-unit">
          <img src="/hardware/fl-02-top.jpg" alt="FL-02 from above: Free-WiLi screen reading FL-02 IDLE, motor drivers and an AprilTag" loading="lazy" />
          <figcaption><b>FL-02, top</b><span>The Free-WiLi screen shows which forklift it is and what it’s doing. The tag sits at the back.</span></figcaption>
        </figure>
      </div>

      <div className="hw-parts">
        {PARTS.map((p) => <div key={p.name}><b>{p.name}</b><p>{p.what}</p></div>)}
      </div>
    </>
  )
}
