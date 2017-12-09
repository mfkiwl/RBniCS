# Copyright (C) 2015-2017 by the RBniCS authors
#
# This file is part of RBniCS.
#
# RBniCS is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# RBniCS is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with RBniCS. If not, see <http://www.gnu.org/licenses/>.
#

from itertools import product as cartesian_product
from rbnics.backends import LinearSolver, SnapshotsMatrix, transpose
from rbnics.backends.online import OnlineAffineExpansionStorage, OnlineFunction
from rbnics.utils.decorators import is_training_finished, PreserveClassName, ReducedProblemDecoratorFor
from backends.online import OnlineMatrix, OnlineSolveKwargsGenerator
from .online_rectification import OnlineRectification

@ReducedProblemDecoratorFor(OnlineRectification)
def OnlineRectificationDecoratedReducedProblem(EllipticCoerciveReducedProblem_DerivedClass):
    
    @PreserveClassName
    class OnlineRectificationDecoratedReducedProblem_Class(EllipticCoerciveReducedProblem_DerivedClass):
        def __init__(self, truth_problem, **kwargs):
            # Call to parent
            EllipticCoerciveReducedProblem_DerivedClass.__init__(self, truth_problem, **kwargs)
            
            # Projection of truth and reduced snapshots
            self.snapshots_mu = list()
            self.snapshots = SnapshotsMatrix(truth_problem.V)
            
            # Extend allowed keywords argument in solve
            self._online_solve_default_kwargs["online_rectification"] = True
            self.OnlineSolveKwargs = OnlineSolveKwargsGenerator(**self._online_solve_default_kwargs)
            
            # Generate all combinations of allowed keyword arguments in solve
            online_solve_kwargs_with_rectification = list()
            online_solve_kwargs_without_rectification = list()
            for other_args in cartesian_product((True, False), repeat=len(self._online_solve_default_kwargs) - 1):
                args_with_rectification = self.OnlineSolveKwargs(*(other_args + (True, )))
                args_without_rectification = self.OnlineSolveKwargs(*(other_args + (False, )))
                online_solve_kwargs_with_rectification.append(args_with_rectification)
                online_solve_kwargs_without_rectification.append(args_without_rectification)
            self.online_solve_kwargs_with_rectification = online_solve_kwargs_with_rectification
            self.online_solve_kwargs_without_rectification = online_solve_kwargs_without_rectification
            
        def _init_operators(self, current_stage="online"):
            # Initialize additional reduced operators
            if current_stage == "online":
                self.operator["projection_truth_snapshots"] = self.assemble_operator("projection_truth_snapshots", "online")
                self.operator["projection_reduced_snapshots"] = self.assemble_operator("projection_reduced_snapshots", "online")
            elif current_stage == "offline":
                self.operator["projection_truth_snapshots"] = OnlineAffineExpansionStorage(1)
                assert len(self.online_solve_kwargs_with_rectification) is len(self.online_solve_kwargs_without_rectification)
                self.operator["projection_reduced_snapshots"] = OnlineAffineExpansionStorage(len(self.online_solve_kwargs_with_rectification))
            # Call Parent
            EllipticCoerciveReducedProblem_DerivedClass._init_operators(self, current_stage)
            
        def build_reduced_operators(self):
            # Call Parent
            EllipticCoerciveReducedProblem_DerivedClass.build_reduced_operators(self)
            # Compute projection of truth and reduced snapshots
            self.operator["projection_truth_snapshots"] = self.assemble_operator("projection_truth_snapshots", "offline")
            self.operator["projection_reduced_snapshots"] = self.assemble_operator("projection_reduced_snapshots", "offline")
            
        def assemble_operator(self, term, current_stage="online"):
            if term == "projection_truth_snapshots" or term == "projection_reduced_snapshots":
                assert current_stage in ("online", "offline")
                if current_stage == "online": # load from file
                    self.operator[term] = OnlineAffineExpansionStorage(1)
                    self.operator[term].load(self.folder["reduced_operators"], term)
                    return self.operator[term]
                elif current_stage == "offline":
                    N = self.N
                    if term == "projection_truth_snapshots":
                        print("build projection truth snapshots for rectification")
                        assert len(self.truth_problem.inner_product) == 1 # the affine expansion storage contains only the inner product matrix
                        X = self.truth_problem.inner_product[0]
                        assert len(self.inner_product) == 1 # the affine expansion storage contains only the inner product matrix
                        X_N = self.inner_product[0]
                        
                        Z = self.Z
                        
                        projection_truth_snapshots = OnlineMatrix(N, N)
                        for (i, snapshot_i) in enumerate(self.snapshots):
                            projected_truth_snapshot_i = OnlineFunction(N)
                            solver = LinearSolver(X_N, projected_truth_snapshot_i, transpose(Z)*X*snapshot_i)
                            solver.solve()
                            for j in range(N):
                                projection_truth_snapshots[j, i] = projected_truth_snapshot_i.vector()[j]
                        # Store and save
                        self.operator["projection_truth_snapshots"][0] = projection_truth_snapshots
                        self.operator["projection_truth_snapshots"].save(self.folder["reduced_operators"], "projection_truth_snapshots")
                    elif term == "projection_reduced_snapshots":
                        print("build projection reduced snapshots for rectification")
                        # Backup mu
                        bak_mu = self.mu
                        # Prepare rectification for all possible online solve arguments
                        for (q, online_solve_kwargs) in enumerate(self.online_solve_kwargs_without_rectification):
                            projection_reduced_snapshots = OnlineMatrix(N, N)
                            for (i, mu_i) in enumerate(self.snapshots_mu):
                                self.set_mu(mu_i)
                                projected_reduced_snapshot_i = self.solve(N, **online_solve_kwargs)
                                for j in range(N):
                                    projection_reduced_snapshots[j, i] = projected_reduced_snapshot_i.vector()[j]
                            self.operator["projection_reduced_snapshots"][q] = projection_reduced_snapshots
                        # Save and restore previous mu
                        self.operator["projection_reduced_snapshots"].save(self.folder["reduced_operators"], "projection_reduced_snapshots")
                        self.set_mu(bak_mu)
                    return self.operator[term]
                else:
                    raise ValueError("Invalid stage in assemble_operator().")
            else:
                return EllipticCoerciveReducedProblem_DerivedClass.assemble_operator(self, term, current_stage)
            
        def _solve(self, N, **kwargs):
            online_solve_kwargs = self.OnlineSolveKwargs(**kwargs)
            if is_training_finished(self.truth_problem):
                # Solve reduced problem
                EllipticCoerciveReducedProblem_DerivedClass._solve(self, N, **online_solve_kwargs)
                if online_solve_kwargs["online_rectification"]:
                    q = self.online_solve_kwargs_with_rectification.index(online_solve_kwargs)
                    intermediate_solution = OnlineFunction(N)
                    solver = LinearSolver(self.operator["projection_reduced_snapshots"][q][:N, :N], intermediate_solution, self._solution.vector())
                    solver.solve()
                    self._solution = self.operator["projection_truth_snapshots"][0][:N, :N]*intermediate_solution
            else:
                EllipticCoerciveReducedProblem_DerivedClass._solve(self, N, **online_solve_kwargs)
            
    # return value (a class) for the decorator
    return OnlineRectificationDecoratedReducedProblem_Class
