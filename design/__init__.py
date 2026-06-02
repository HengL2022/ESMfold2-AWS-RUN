"""Gradient-based VHH binder-design package for ESMFold2 (the 'hallucination' loop).

iPTM is a weak/non-specific oracle for CD5 (see project memory: Gate-0), so the design objective is
the distogram intra/inter-contact losses + ESMC prior + explicit hotspot restriction; iPTM is only a
coarse ensemble filter. model_access.py is the differentiable substrate everything else builds on.
"""
