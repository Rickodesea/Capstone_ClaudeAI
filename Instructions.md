One final thing to add.
In our optimization model in the math, we will use waves (trigonometry). Getting prediction of memory use and memory spikes we will treat as waves (spikes as waves with high amplitude and low average memory use as flatline waves). This will simplify the math because waves are additive. We can sum all the predictions of the VMs on the node to have a combined wave form. Waves is tracked over a time period. We look for spikes that exceed the physical ram at a future time and make adjustments before. 
Sometimes we may not need to adjust if like the life time of a vm end (if it was short live). So we need a variable to determine how far ahead before the predicted peak we should act (share or terminate) – 1 hour, 30 min, 5 min, etc.
Is this idea novel? Math make sense? Defendable?
Add thoroughly to the proposal.
Add to powerpoint.
Create a markdown with diagrams displaying the structure, components, core, simulation of the project.
