use std::cmp::{min, max};
use std::{i64, usize};

use geom::point::Point;
use num::NumberOps;

use super::rasterizer::Rasterizer;
use super::edge::{Edge, EdgeType};
use super::scene::Scene;


const HALF_MAX_ERR: i64  = i64::MAX / 2;


#[derive(Debug, Clone)]
pub struct IntersectionRef {
   pub first_px: i64,
   pub start: usize,
   pub end: usize,
}

impl IntersectionRef {
   #[inline]
   pub fn new(first_px: i64, start: usize, end: usize) -> Self {
      IntersectionRef {
         first_px: first_px,
         start: start,
         end: end,
      }
   }
}

impl Default for IntersectionRef {
   #[inline]
   fn default() -> IntersectionRef {
      IntersectionRef::new(0, 0, 0)
   }
}


pub trait RasterizerIntersection {
   fn reset_intersections(&mut self, scene: &Scene);

   fn intersect_edges(&mut self, scene: &Scene);

   fn check_intersections(&self, scene: &Scene);

   fn h_intersection(&self, edge: &Edge, y_px: i64) -> i64;

   fn v_intersection(&self, edge: &Edge, x_px: i64) -> i64;
}


impl RasterizerIntersection for Rasterizer {
   fn reset_intersections(&mut self, scene: &Scene) {
      for i in 0..scene.segments.len() {
         self.vert_intersections_ref[i].start = usize::MAX;
         self.hori_intersections_ref[i].start = usize::MAX;
      }
   }

   fn intersect_edges(&mut self, scene: &Scene) {
      self.reset_intersections(scene);

      let mut vert_prev_end = 0;
      let mut hori_prev_end = 0;

      for edge in &scene.edges {
         match edge.edge_type {
            EdgeType::LTR | EdgeType::LTL | EdgeType::LBR | EdgeType::LBL => {

               let segment_index = edge.segment;

               let ref mut vert_ref = self.vert_intersections_ref[segment_index];
               if vert_ref.start != usize::MAX {
                  continue;
               }

               let ref mut hori_ref = self.hori_intersections_ref[segment_index];

               let ref segment = scene.segments[segment_index];
               let ref p1 = scene.points[segment.p1];
               let ref p2 = scene.points[segment.p2];

               vert_ref.start = vert_prev_end;
               hori_ref.start = hori_prev_end;

               let (vert_end, x_first_px) = v_multi_intersect_fast(
                  p1, p2, self.div_per_pixel, vert_ref.start, &mut self.vert_intersections
               );

               let (hori_end, y_first_px) = h_multi_intersect_fast(
                  p1, p2, self.div_per_pixel, hori_ref.start, &mut self.hori_intersections
               );

               vert_prev_end = vert_end;
               vert_ref.end = vert_end;
               vert_ref.first_px = x_first_px;

               hori_prev_end = hori_end;
               hori_ref.end = hori_end;
               hori_ref.first_px = y_first_px;
            },
            EdgeType::CTR | EdgeType::CTL | EdgeType::CBR | EdgeType::CBL => {

               let segment_index = edge.segment;

               let ref mut vert_ref = self.vert_intersections_ref[segment_index];
               if vert_ref.start != usize::MAX {
                  continue;
               }

               let ref mut hori_ref = self.hori_intersections_ref[segment_index];

               let ref segment = scene.segments[segment_index];
               let ref p1 = scene.points[segment.p1];
               let ref p2 = scene.points[segment.p2];

               hori_ref.start = hori_prev_end;
               vert_ref.start = vert_prev_end;

               let ref circle = scene.circles[edge.circle];
               let ref center = scene.points[circle.center];
               let radius = circle.radius;

               let start = 1 + p1.y / self.div_per_pixel;
               let end = 1 + (p2.y - 1) / self.div_per_pixel;

               debug_assert!(p1.y <= p2.y);

               for y_px in start..end {
                  let y = y_px * self.div_per_pixel;
                  let dy = y - center.y;

                  debug_assert!(radius > dy.abs());

                  let dx = (radius * radius - dy * dy).sqrt();

                  debug_assert!(dx > 0);

                  let x = match edge.edge_type {
                     EdgeType::CTR | EdgeType::CTL => center.x - dx,
                     _ => center.x + dx
                  };

                  self.hori_intersections[hori_prev_end] = x;
                  hori_prev_end += 1;

               }

               hori_ref.end = hori_prev_end;
               hori_ref.first_px = start;

               let (x1, x2) = match edge.edge_type {
                  EdgeType::CTR | EdgeType::CBL => (p1.x, p2.x),
                  _ => (p2.x, p1.x),
               };

               debug_assert!(x1 <= x2);

               let start = 1 + x1 / self.div_per_pixel;
               let end = 1 + (x2 - 1) / self.div_per_pixel;

               for x_px in start..end {
                  let x = x_px * self.div_per_pixel;
                  let dx = center.x - x;

                  debug_assert!(radius > dx.abs());

                  let dy = (radius * radius - dx * dx).sqrt();

                  debug_assert!(dy > 0);

                  let y = match edge.edge_type {
                     EdgeType::CTR | EdgeType::CBR => center.y + dy,
                     _ => center.y - dy
                  };

                  self.vert_intersections[vert_prev_end] = y;
                  vert_prev_end += 1;
               }

               vert_ref.end = vert_prev_end;
               vert_ref.first_px = start;
            },
            _ => {}
         }
      }
   }

   fn check_intersections(&self, scene: &Scene) {
      for edge in &scene.edges {
         match edge.edge_type {
            EdgeType::LTR | EdgeType::LTL | EdgeType::LBR | EdgeType::LBL |
            EdgeType::CTR | EdgeType::CTL | EdgeType::CBR | EdgeType::CBL |
            EdgeType::ATR | EdgeType::ATL | EdgeType::ABR | EdgeType::ABL => {
               let ref segment = scene.segments[edge.segment];
               let ref p1 = scene.points[segment.p1];
               let ref p2 = scene.points[segment.p2];

               let min_x = min(p1.x, p2.x);
               let max_x = max(p1.x, p2.x);
               let min_y = min(p1.y, p2.y);
               let max_y = max(p1.y, p2.y);

               let ref vert_ref = self.vert_intersections_ref[edge.segment];

               let mut prev_y = i64::MIN;
               for i in vert_ref.start..vert_ref.end {
                  let y = self.vert_intersections[i];
                  debug_assert!(min_y <= y);
                  debug_assert!(max_y >= y);
                  debug_assert!(prev_y < y);
                  prev_y = y;
               }

               let ref hori_ref = self.hori_intersections_ref[edge.segment];

               let mut prev_x = i64::MIN;
               for i in hori_ref.start..hori_ref.end {
                  let x = self.hori_intersections[i];
                  debug_assert!(min_x <= x);
                  debug_assert!(max_x >= x);
                  debug_assert!(prev_x < x);
                  prev_x = x;
               }
            },
            _ => {}
         }
      }
   }

   #[inline]
   fn h_intersection(&self, edge: &Edge, y_px: i64) -> i64 {
      if edge.edge_type == EdgeType::LVT || edge.edge_type == EdgeType::LVB {
         return edge.p1.x;
      }

      let ref h_ref = self.hori_intersections_ref[edge.segment];

      debug_assert!(y_px >= h_ref.first_px);
      debug_assert!(h_ref.start != usize::MAX);

      self.hori_intersections[
         h_ref.start + (y_px - h_ref.first_px) as usize
      ]
   }

   #[inline]
   fn v_intersection(&self, edge: &Edge, x_px: i64) -> i64 {
      if edge.edge_type == EdgeType::LHR || edge.edge_type == EdgeType::LHL {
         return edge.p1.y;
      }

      let ref v_ref = self.vert_intersections_ref[edge.segment];

      debug_assert!(x_px >= v_ref.first_px);
      debug_assert!(v_ref.start != usize::MAX);

      self.vert_intersections[
         v_ref.start + (x_px - v_ref.first_px) as usize
      ]
   }
}


fn h_multi_intersect_fast(p1: &Point, p2: &Point, step_y: i64, mut vec_start: usize, inters: &mut Vec<i64>) -> (usize, i64) {
   let (p1, p2) = if p1.y > p2.y {
      (p2, p1)
   } else {
      (p1, p2)
   };

   let start = 1 + p1.y / step_y;
   let end = 1 + (p2.y - 1) / step_y;

   let dy = p2.y - p1.y;
   let dx = p2.x - p1.x;
   let dx_signum = dx.signum();

   let step_x = dx * step_y / dy;

   let max_div_dy = i64::MAX / dy;

   let err_step = max_div_dy * (step_y * dx * dx_signum - step_x * dx_signum * dy);

   let first_y = start * step_y;

   let fdy = first_y - p1.y;
   let fdx = dx * fdy / dy;

   let mut x = p1.x + fdx;

   if err_step == 0 {
      for _ in start..end {
         inters[vec_start] = x;
         vec_start += 1;

         x += step_x;
      }

      return (vec_start, start);
   }

   let mut err = max_div_dy * (fdy * dx * dx_signum - fdx * dx_signum * dy) - HALF_MAX_ERR;

   for _ in start..end {
      if err > 0 {
         x += dx_signum;
         err -= i64::MAX;
      }

      inters[vec_start] = x;
      vec_start += 1;

      x += step_x;

      err += err_step;
   }

   (vec_start, start)
}


fn v_multi_intersect_fast(p1: &Point, p2: &Point, step_x: i64, mut vec_start: usize, inters: &mut Vec<i64>) -> (usize, i64) {
   let (p1, p2) = if p1.x > p2.x {
      (p2, p1)
   } else {
      (p1, p2)
   };

   let start = 1 + p1.x / step_x;
   let end = 1 + (p2.x - 1) / step_x;

   let dx = p2.x - p1.x;
   let dy = p2.y - p1.y;
   let dy_signum = dy.signum();

   let step_y = dy * step_x / dx;

   let max_div_dx = i64::MAX / dx;

   let err_step = max_div_dx * (step_x * dy * dy_signum - step_y * dy_signum * dx);

   let first_x = start * step_x;

   let fdx = first_x - p1.x;
   let fdy = dy * fdx / dx;

   let mut y = p1.y + fdy;

   if err_step == 0 {
      for _ in start..end {
         inters[vec_start] = y;
         vec_start += 1;

         y += step_y;
      }

      return (vec_start, start);
   }

   let mut err = max_div_dx * (fdx * dy * dy_signum - fdy * dy_signum * dx) - HALF_MAX_ERR;

   for _ in start..end {
      if err > 0 {
         y += dy_signum;
         err -= i64::MAX;
      }

      inters[vec_start] = y;
      vec_start += 1;

      y += step_y;

      err += err_step;
   }

   (vec_start, start)
}

